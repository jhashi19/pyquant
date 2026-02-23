from datetime import date

from app.engine.math.bizday import BusinessCalendar, add_business_days, adjust_business_day
from app.engine.products.models.schedule_models import (
    RefRateRule,
    SwapScheduleRow,
    TradeHeader,
    TradeIRS,
    TradeIRSAmortizingStep,
)
from app.engine.products.swap_schedule import build_swap_schedule_rows


def _trade() -> TradeHeader:
    return TradeHeader(
        trade_id="SWAP_1",
        product="IRS",
        ccy="USD",
        notional=100_000_000.0,
        buy_sell="BUY",
        trade_date=date(2026, 1, 2),
        effective_date=date(2026, 1, 2),
        maturity_date=date(2026, 7, 2),
    )


def _irs() -> TradeIRS:
    return TradeIRS(
        trade_id="SWAP_1",
        pay_rec="PAY",
        fixed_rate=0.02,
        fixed_daycount="ACT/360",
        fixed_freq="3M",
        fixed_bdc="MF",
        fixed_cal_id="USNY",
        float_index_id="USD-SOFR-3M",
        float_spread=0.001,
        float_daycount="ACT/360",
        float_freq="3M",
        float_bdc="MF",
        float_cal_id="USNY",
        stub_type="BACK",
        settle_ccy="USD",
    )


def data_factory(
    *,
    trade: TradeHeader | None = None,
    irs: TradeIRS | None = None,
    rule: RefRateRule | None = None,
    amortizing_steps: tuple[TradeIRSAmortizingStep, ...] = (),
    calendars: dict[str, BusinessCalendar] | None = None,
) -> list[SwapScheduleRow]:
    trade_obj = trade or _trade()
    irs_obj = irs or _irs()
    rule_obj = rule or RefRateRule(
        index_id="USD-SOFR-3M",
        rate_type="TERM",
        accrual_conv="SIMPLE",
        daycount="ACT/360",
        fixing_cal_id="USNY",
        fixing_bdc="MF",
        lookback_days=0,
    )
    cal_map = calendars or {"USNY": BusinessCalendar()}
    return build_swap_schedule_rows(
        trade_obj,
        irs_obj,
        rule_obj,
        calendars=cal_map,
        amortizing_steps=amortizing_steps,
    )


def test_swap_schedule_supports_amortizing_notional() -> None:
    rows = data_factory(
        amortizing_steps=(
            TradeIRSAmortizingStep(
                trade_id="SWAP_1",
                step_no=1,
                change_date=date(2026, 6, 1),
                notional_ratio=0.5,
            ),
        ),
    )

    fixed = [r for r in rows if r.leg_id == "FIXED"]
    floating = [r for r in rows if r.leg_id == "FLOAT"]

    assert [r.notional for r in fixed] == [100_000_000.0, 50_000_000.0]
    assert [r.notional for r in floating] == [100_000_000.0, 50_000_000.0]


def test_swap_schedule_sets_ois_observation_window_with_lookback() -> None:
    trade = TradeHeader(
        trade_id="SWAP_2",
        product="IRS",
        ccy="USD",
        notional=10_000_000.0,
        buy_sell="BUY",
        trade_date=date(2026, 1, 2),
        effective_date=date(2026, 1, 2),
        maturity_date=date(2026, 7, 2),
    )
    irs = TradeIRS(
        trade_id="SWAP_2",
        pay_rec="REC",
        fixed_rate=0.02,
        fixed_daycount="ACT/360",
        fixed_freq="6M",
        fixed_bdc="MF",
        fixed_cal_id="USNY",
        float_index_id="USD-SOFR-6M",
        float_spread=0.0,
        float_daycount="ACT/360",
        float_freq="6M",
        float_bdc="MF",
        float_cal_id="USNY",
        stub_type="BACK",
        settle_ccy="USD",
    )
    rule = RefRateRule(
        index_id="USD-SOFR-6M",
        rate_type="ON",
        accrual_conv="COMPOUND_IN_ARREARS",
        daycount="ACT/360",
        fixing_cal_id="USNY",
        fixing_bdc="MF",
        lookback_days=2,
    )
    calendar = BusinessCalendar()

    rows = data_factory(
        trade=trade,
        irs=irs,
        rule=rule,
        calendars={"USNY": calendar},
    )

    float_row = next(r for r in rows if r.leg_id == "FLOAT")
    assert float_row.start_date is not None
    assert float_row.end_date is not None

    expected_obs_start = adjust_business_day(
        add_business_days(float_row.start_date, -2, calendar),
        "MF",
        calendar,
    )
    expected_obs_end = adjust_business_day(
        add_business_days(float_row.end_date, -2, calendar),
        "MF",
        calendar,
    )

    assert float_row.rate_calc_type == "OIS_COMPOUNDED"
    assert float_row.obs_start_date == expected_obs_start
    assert float_row.obs_end_date == expected_obs_end
