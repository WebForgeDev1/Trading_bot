"""Paper fills only: buy ASK, mark/exit BID. Never contacts a broker."""
from strategy_n1 import StrategyN1


def open_paper(signal_day, tick, config):
    strategy = StrategyN1()
    levels = strategy.calculate_levels(tick.ask)
    return dict(id='N1-'+signal_day, symbol='XAUUSD', direction='BUY', signal_date=signal_day,
                entry_time=tick.ts, **levels, exposure=strategy.risk_usd/strategy.sl_distance,
                status='OPEN', source=tick.source, price_side='ASK_BID', mode='PAPER_ONLY',
                entry_bid=tick.bid, entry_ask=tick.ask, entry_spread=tick.ask-tick.bid,
                commission_per_unit_per_side_usd=config.commission_per_unit_per_side_usd,
                financing_per_unit_per_day_usd=config.financing_per_unit_per_day_usd,
                cost_model='linear_elapsed_days_estimate')


def close_paper(position, tick, outcome, recovered):
    trade = dict(position)
    gross = round((tick.bid-position['entry'])*position['exposure'],2)
    commission_rate = position.get('commission_per_unit_per_side_usd')
    financing_rate = position.get('financing_per_unit_per_day_usd')
    commission = None if commission_rate is None else round(2*position['exposure']*commission_rate,2)
    duration_days = (tick.ts-position['entry_time'])/86400000
    financing = None if financing_rate is None else round(duration_days*position['exposure']*financing_rate,2)
    complete = commission is not None and financing is not None and position.get('price_side')=='ASK_BID'
    known_costs = (commission or 0)+(financing or 0)
    trade.update(status='CLOSED_'+outcome, exit_time=tick.ts, exit=tick.bid, exit_bid=tick.bid,
                 exit_ask=tick.ask, outcome=outcome, recovered=recovered, pnl_usd=gross,
                 commission_usd=commission, financing_usd=financing,
                 known_costs_usd=round(known_costs,2), costs_complete=complete,
                 estimated_net_pnl_usd=round(gross-known_costs,2) if complete else None)
    # Spread is already paid by buying ASK/selling BID: do not subtract twice.
    trade['entry_spread_cost_usd'] = round(position.get('entry_spread',0)*position['exposure'],2)
    return trade
