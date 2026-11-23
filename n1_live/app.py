import argparse
import csv
import json
import logging
from logging.handlers import RotatingFileHandler
import signal
import sys
import threading
import time
import sqlite3
from datetime import timedelta
from pathlib import Path

from .analytics import statistics
from .calendar import dt, PARIS, day_bounds
from .config import ROOT, load_config
from .engine import Engine
from .market import Dukascopy, candle
from .storage import Store, process_lock, restore_database
from .control import halted, emergency_stop, clear_emergency, EmergencyStop
from .mt5_readonly import MT5ReadOnlyProvider, environment_report, read_quotes


def now_ms():
    return int(time.time()*1000)


def show(state, config, store=None):
    if not state:
        print('SIMULATION ACTUALISÉE — pas encore démarrée')
        return
    now = now_ms()
    heartbeat_age = max(0,(now-state['heartbeat'])//1000)
    print('\n'+'='*72)
    print('XAUUSD N°1 — PAPER ONLY | analyse Dukascopy | cotations '+config.quote_source, flush=True)
    if halted(config.db_path):
        print('ARRÊT D’URGENCE VERROUILLÉ — reprise explicite nécessaire')
    print('Connexion enregistrée :', state['connection'], '| dernier contrôle :', heartbeat_age, 's')
    if heartbeat_age > max(config.poll_seconds*3,config.request_timeout_seconds+config.poll_seconds):
        print('ALERTE : suivi arrêté ou contrôle trop ancien ; surveillance non confirmée')
    quote = state.get('latest_received_quote') or state['last_quote']
    if quote:
        age = max(0,(now-quote['timestamp'])//1000)
        print(f"BID {quote['bid']:.3f} $ | ASK {quote['ask']:.3f} $ | spread {quote['ask']-quote['bid']:.3f} $")
        print(f"Cotation : {dt(quote['timestamp']).astimezone(PARIS).isoformat()} | âge {age} s")
        if age > config.stale_seconds:
            print('Cotation ancienne : ne pas considérer ce prix comme actuel')
    else:
        print('Aucune cotation reçue')
    print('Dernière journée analysée :',state['last_finalized_day'] or 'en attente du premier historique M1')
    summary = state.get('last_market_day_summary')
    if summary:
        print('Dernière journée de marché :', summary['day'], '|', summary['quality'], '| minutes', summary['observed_minutes'], '/', summary['expected_minutes'], '| manquantes', summary['missing_minutes'])
    progress = state.get('current_day_progress')
    if progress:
        print('Journée en cours :', progress['day'], '| M1 valides', progress['observed_minutes'], '/', progress['expected_minutes'], '| analyse après minuit Paris')
    if state.get('waiting_day'):
        print('Analyse en attente des dernières M1 :',state['waiting_day'])
    print('Entrée en attente :',state['pending'] or 'aucune')
    position=state['position']
    if position:
        print(f"POSITION FICTIVE {position['id']} — {position['status']}")
        print(f"BUY {position['entry']:.3f} | SL {position['sl']:.3f} | TP {position['tp']:.3f} | unités {position['exposure']}")
        if quote:
            print(f"P&L flottant brut indicatif : {(quote['bid']-position['entry'])*position['exposure']:+.2f} $")
    else:
        print('Position fictive : aucune')
    if store:
        trades,signals=store.records('trades'),store.records('signals')
        metrics=statistics(trades,signals)
        latest=max(signals,key=lambda s:s['day']) if signals else None
        print('Dernier signal :', (latest['day']+' — '+latest['status']+' — '+latest['reason']) if latest else 'aucun')
        closed=[t for t in trades if t.get('exit_time')]
        last=max(closed,key=lambda t:t['exit_time']) if closed else None
        print('Dernière clôture :', (last['id']+' — '+last['status']+' — '+str(last['pnl_usd'])+' $ brut') if last else 'aucune')
        print('Trades clôturés :',metrics['closed_trades'],'| résultat brut :',metrics['gross_pnl'],'$')
        print('Résultat net estimé :', str(metrics['estimated_net_pnl'])+' $' if metrics['estimated_net_pnl'] is not None else 'indisponible (coûts non renseignés ou aucun trade)')
    print('Exécution : achat ASK / sortie BID ; commissions et financement :',
          config.commission_per_unit_per_side_usd if config.commission_per_unit_per_side_usd is not None else 'inconnus', '/',
          config.financing_per_unit_per_day_usd if config.financing_per_unit_per_day_usd is not None else 'inconnus')
    if state['last_error']:
        print('ALERTE :',state['last_error'])
    print(flush=True)


class Poller:
    """One real acquisition/processing cycle; also exercised by integration tests."""
    def __init__(self, engine, provider):
        self.engine, self.provider = engine, provider
        self.config = engine.config
        self.last_bars_at, self.bars = 0, []

    def step(self, started=None, received_at=None):
        started = now_ms() if started is None else started
        state = self.engine.store.state()
        since = (state['last_quote']['timestamp'] if state['last_quote'] else state['started_at']) if state else started-60000
        minimum = started-self.config.recovery_hours*3600000
        lost = since < minimum
        since = max(since, minimum)
        ticks = self.provider.read('tick', since, started)
        current_day = dt(started).astimezone(PARIS).date()
        rollover = state and state['current_day'] != current_day.isoformat()
        warning = None
        if started-self.last_bars_at >= 60000 or rollover:
            history_start = current_day-timedelta(days=self.config.history_days)
            if state:
                history_start = min(history_start, dt(since).astimezone(PARIS).date())
            try:
                self.bars = self.provider.read('m1', day_bounds(history_start)[0], started)
                self.last_bars_at = started
            except (OSError, ValueError, RuntimeError) as exc:
                warning = str(exc)
        # A daily-data outage must not prevent SL/TP monitoring on valid ticks.
        state, count = self.engine.cycle(ticks, self.bars,
            now_ms() if received_at is None else received_at,
            recovery_lost=lost, bars_asof=self.last_bars_at,
            processing_clock=now_ms if received_at is None else lambda: received_at)
        if warning:
            with self.engine.store.transaction():
                state['last_error'] = 'Bougies M1 indisponibles : '+warning
                self.engine.store.event(state['heartbeat'], 'DAILY_DATA_ERROR', message=state['last_error'])
                state['last_event_id'] = self.engine.store.db.execute('SELECT MAX(id) FROM events').fetchone()[0]
                self.engine.store.save(state)
        return state, count, warning


def run(config, once=False):
    path = config.db_path
    with process_lock(path.with_suffix('.run.lock')):
        store = Store(path)
        engine=Engine(store,config)
        provider=MT5ReadOnlyProvider(config) if config.quote_source=='mt5_csv' else Dukascopy(config)
        poller = Poller(engine, provider)
        stop = threading.Event()
        provider.cancel_check=lambda:stop.is_set() or halted(path)
        old_handlers = {}
        for sig in (signal.SIGINT,signal.SIGTERM):
            old_handlers[sig] = signal.signal(sig,lambda *_:stop.set())
        logger = logging.getLogger('n1_live')
        logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(path.parent/'technical.log',maxBytes=2_000_000,backupCount=3)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
        failures = 0
        print('SIMULATION UNIQUEMENT — collecte Internet ; Ctrl+C pour arrêter.', flush=True)
        try:
            daily_backup=path.parent/'backups'/('daily-'+dt(now_ms()).astimezone(PARIS).strftime('%Y-%m-%d')+'.sqlite3')
            if not daily_backup.exists():
                store.backup(daily_backup)
            while not stop.is_set():
                if halted(path):
                    break
                started = now_ms()
                state = store.state()
                previous_event = state['last_event_id'] if state else 0
                try:
                    state,count,warning = poller.step(started)
                    failures = 0
                    logger.info('cycle ticks=%s connection=%s',count,state['connection'])
                    show(state,config,store)
                    events = store.db.execute('SELECT kind,payload FROM events WHERE id>? ORDER BY id', (previous_event,)).fetchall()
                    for event in events:
                        print('SIMULATION —', event['kind'], event['payload'], flush=True)
                    if config.bell and events:
                        print('\a',end='',flush=True)
                    if once and warning:
                        return 1
                except (OSError,ValueError,KeyError,RuntimeError) as exc:
                    if isinstance(exc,EmergencyStop) or (isinstance(exc,InterruptedError) and (stop.is_set() or halted(path))):
                        break
                    failures += 1
                    message = str(exc)
                    engine.error(now_ms(),message)
                    logger.error('%s: %s',type(exc).__name__,message)
                    print('ALERTE —',message,'| nouvelle tentative automatique',flush=True)
                    if once:
                        return 1
                if once:
                    return 0
                delay = min(300,config.poll_seconds*(2**min(failures,4)))
                remaining=delay
                while remaining>0 and not stop.is_set() and not halted(path):
                    interval=min(.25,remaining)
                    stop.wait(interval)
                    remaining-=interval
        finally:
            with store.transaction():
                state = store.state()
                if state:
                    state.update(connection='EMERGENCY_STOP' if halted(path) else 'STOPPED',heartbeat=now_ms())
                    store.event(now_ms(),state['connection'],message='Arrêt du processus ; aucune surveillance en arrière-plan')
                    state['last_event_id']=store.db.execute('SELECT MAX(id) FROM events').fetchone()[0]
                    store.save(state)
            logger.removeHandler(handler)
            handler.close()
            store.close()
            for sig,old in old_handlers.items():
                signal.signal(sig,old)
            print('Suivi arrêté. État sauvegardé.'+(' Arrêt d’urgence verrouillé.' if halted(path) else ''),flush=True)
    return 0


def export(store,destination):
    destination = Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    for table in ('trades','signals','days'):
        rows = store.records(table)
        (destination/(table+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        if rows:
            columns = sorted({key for row in rows for key in row})
            with (destination/(table+'.csv')).open('w',newline='') as file:
                writer=csv.DictWriter(file,fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
    events = [dict(r) for r in store.db.execute('SELECT * FROM events ORDER BY id')]
    (destination/'events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
    (destination/'statistics.json').write_text(json.dumps(statistics(store.records('trades'),store.records('signals')),indent=2,ensure_ascii=False))
    print('Export :',destination)


def audit_csv(path):
    import pandas as pd
    frame=pd.read_csv(path)
    if frame.empty:
        raise ValueError('CSV vide')
    previous=-1
    for row in frame.to_dict('records'):
        data=candle(row)
        if data['timestamp'] <= previous:
            raise ValueError('CSV hors ordre ou contenant des doublons')
        previous=data['timestamp']
    print('CSV valide :',len(frame),'bougies M1. La couverture des journées reste à vérifier.')


def main(argv=None):
    parser=argparse.ArgumentParser(description='XAUUSD N°1 — simulation uniquement')
    parser.add_argument('--config',type=Path,default=ROOT/'bot_config.json')
    commands=parser.add_subparsers(dest='command')
    live=commands.add_parser('run',help='Suivre les prix Internet et simuler automatiquement')
    live.add_argument('--once',action='store_true',help='Un seul cycle puis arrêt')
    for name in ('status','dashboard','position','signals','trades','days','stats','events','emergency-stop','resume','mt5-status'):
        commands.add_parser(name)
    backup=commands.add_parser('backup')
    backup.add_argument('destination',type=Path,nargs='?')
    restore=commands.add_parser('restore')
    restore.add_argument('source',type=Path)
    restore.add_argument('--confirm-restore',action='store_true')
    out=commands.add_parser('export')
    out.add_argument('directory',type=Path,nargs='?',default=ROOT/'runtime/exports')
    check=commands.add_parser('check-csv')
    check.add_argument('csv',type=Path)
    replay=commands.add_parser('replay')
    replay.add_argument('--csv',type=Path)
    replay.add_argument('--output',type=Path,default=ROOT/'runtime/replay_results.csv')
    args=parser.parse_args(argv)
    try:
        config=load_config(args.config)
        command=args.command or 'run'
        if command=='emergency-stop':
            print('Arrêt d’urgence demandé :',emergency_stop(config.db_path))
            print('Aucune clôture fictive inventée. Reprise : python3 main.py resume')
            return 0
        if command=='resume':
            with process_lock(config.db_path.with_suffix('.run.lock')):
                clear_emergency(config.db_path)
            print('Verrou levé. Démarrer explicitement : python3 main.py run')
            return 0
        if command=='mt5-status':
            report=environment_report()
            if config.mt5_csv_path:
                quotes=read_quotes(config.mt5_csv_path,config.mt5_symbol)
                report['last_quote']=quotes[-1] if quotes else None
                report['fresh']=bool(quotes and 0<=now_ms()-quotes[-1]['timestamp']<=config.stale_seconds*1000)
            else:
                report['blocked']='Export local MT5 non configuré ; aucune connexion courtier effectuée'
            print(json.dumps(report,indent=2,ensure_ascii=False))
            return 0
        if command=='restore':
            if not args.confirm_restore:
                raise ValueError('Restauration explicite requise : ajouter --confirm-restore')
            with process_lock(config.db_path.with_suffix('.run.lock')):
                emergency_stop(config.db_path)
                if config.db_path.exists():
                    old=Store(config.db_path)
                    try:old.backup(config.db_path.parent/'backups'/('before-restore-'+str(now_ms())+'.sqlite3'))
                    finally:old.close()
                restore_database(args.source,config.db_path)
            print('Base restaurée ; arrêt d’urgence maintenu. Vérifier puis utiliser resume.')
            return 0
        if command=='run':
            return run(config,getattr(args,'once',False))
        if command=='check-csv':
            audit_csv(args.csv)
            return 0
        if command=='replay':
            from replay_engine import main as replay_main
            protected={ROOT/'n1_replay_results.csv',ROOT/'n1_backtest_2020_2026.csv',ROOT/'n1_backtest_2025.csv'}
            if args.output.resolve() in protected or (ROOT/'data') in args.output.resolve().parents:
                raise ValueError('Destination historique protégée ; choisir un autre export')
            args.output.parent.mkdir(parents=True,exist_ok=True)
            options=['--output',str(args.output)]
            if args.csv:
                options+=['--csv',str(args.csv)]
            replay_main(options)
            return 0
        if not config.db_path.exists():
            print('Aucun état actualisé. Lancer : python3 main.py run')
            return 0
        store=Store(config.db_path)
        try:
            if command=='status':
                show(store.state(),config,store)
            elif command=='dashboard':
                try:
                    while True:
                        if sys.stdout.isatty():print('\033[2J\033[H',end='')
                        show(store.state(),config,store)
                        time.sleep(5)
                except KeyboardInterrupt:pass
            elif command=='backup':
                destination=args.destination or config.db_path.parent/'backups'/('manual-'+str(now_ms())+'.sqlite3')
                print('Sauvegarde vérifiée :',store.backup(destination))
            elif command=='position':
                print(json.dumps((store.state() or {}).get('position'),indent=2,ensure_ascii=False))
            elif command in ('signals','trades','days'):
                print(json.dumps(store.records(command),indent=2,ensure_ascii=False))
            elif command=='stats':
                print(json.dumps(statistics(store.records('trades'),store.records('signals')),indent=2,ensure_ascii=False))
            elif command=='events':
                for row in store.db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 50'):
                    print(dict(row))
            elif command=='export':
                export(store,args.directory)
        finally:
            store.close()
        return 0
    except (ValueError,OSError,RuntimeError) as exc:
        parser.error(str(exc))
