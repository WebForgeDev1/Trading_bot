from collections import defaultdict
from statistics import mean
from .calendar import dt, PARIS


def statistics(trades, signals):
    closed = sorted((t for t in trades if t['status'] in ('CLOSED_TP','CLOSED_SL')), key=lambda t:t['exit_time'])
    monthly, yearly = defaultdict(float), defaultdict(float)
    equity, peak, drawdown = 1000., 1000., 0.
    annual_curves = {}
    for trade in closed:
        stamp = dt(trade['exit_time']).astimezone(PARIS)
        month, year = stamp.strftime('%Y-%m'), str(stamp.year)
        pnl = trade['pnl_usd']
        monthly[month] += pnl
        yearly[year] += pnl
        equity += pnl
        peak = max(peak,equity)
        drawdown = max(drawdown,peak-equity)
        curve = annual_curves.setdefault(year,dict(initial_capital=1000.,capital=1000.,peak=1000.,max_drawdown=0.))
        curve['capital'] += pnl
        curve['peak'] = max(curve['peak'],curve['capital'])
        curve['max_drawdown'] = max(curve['max_drawdown'],curve['peak']-curve['capital'])
    wins = sum(t['pnl_usd'] > 0 for t in closed)
    return dict(mode='PAPER_ONLY', basis='BUY ASK / sortie BID pour les nouveaux trades ; anciens trades BID identifiés séparément',
                estimated_net_pnl=round(sum(t['estimated_net_pnl_usd'] for t in closed),2) if closed and all(t.get('estimated_net_pnl_usd') is not None for t in closed) else None,
                cost_complete_trades=sum(bool(t.get('costs_complete')) for t in closed),
                cost_unknown_trades=sum(not t.get('costs_complete',False) for t in closed),
                legacy_bid_trades=sum(t.get('price_side')!='ASK_BID' for t in closed),
                known_costs_usd=round(sum(t.get('known_costs_usd',0) for t in closed),2),
                closed_trades=len(closed), tp=sum(t['status']=='CLOSED_TP' for t in closed),
                sl=sum(t['status']=='CLOSED_SL' for t in closed),
                win_rate_percent=round(100*wins/len(closed),2) if closed else None,
                gross_pnl=round(sum(t['pnl_usd'] for t in closed),2),
                gross_gains=round(sum(max(0,t['pnl_usd']) for t in closed),2),
                gross_losses=round(sum(min(0,t['pnl_usd']) for t in closed),2),
                max_closed_trade_drawdown=round(drawdown,2),
                average_duration_seconds=mean((t['exit_time']-t['entry_time'])/1000 for t in closed) if closed else None,
                monthly=dict(monthly), yearly=dict(yearly), annual_capital_reset=annual_curves,
                unexecuted_signals=sum(s['status'] in ('MISSED','REJECTED') for s in signals),
                pending_signals=sum(s['status']=='ENTRY_PENDING' for s in signals),
                ambiguous_positions=sum(t['status']=='AMBIGUOUS' for t in trades))
