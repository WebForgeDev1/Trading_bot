"""Lanceur de simulation historique persistante, sans connexion courtier."""
import argparse
from pathlib import Path
from market_data import load_market_data
from persistent_simulation import simulate

BASE_DIR = Path(__file__).resolve().parent
FILENAME = 'xauusd-m1-bid-2026-01-01-2026-10-31T23.csv'


def main(argv=None):
    parser = argparse.ArgumentParser(description='XAUUSD N°1 — simulation historique uniquement')
    parser.add_argument('--csv', type=Path)
    parser.add_argument('--state', type=Path, default=BASE_DIR / 'historical_simulation_state.json')
    parser.add_argument('--output', type=Path, default=BASE_DIR / 'historical_simulation_trades.csv')
    args = parser.parse_args(argv)
    path = args.csv or next((BASE_DIR / folder / FILENAME for folder in ('data', 'CSV data')
                            if (BASE_DIR / folder / FILENAME).is_file()), None)
    if path is None:
        parser.error('CSV introuvable ; préciser --csv')
    if path.resolve() in (args.state.resolve(), args.output.resolve()):
        parser.error('Le CSV source doit être distinct des fichiers de sortie')
    try:
        state, count = simulate(load_market_data(path), args.state, args.output, path.resolve())
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    print('XAUUSD N°1 — SIMULATION HISTORIQUE UNIQUEMENT')
    print('Nouvelles minutes traitées :', count)
    print('Signaux retenus :', len(state['signals']))
    print('Trades clôturés :', len(state['trades']))
    print(f"Résultat brut : {sum(t['pnl_usd'] for t in state['trades']):+.2f} $")
    print('Position fictive :', state['position'] or 'aucune')
    print('Dernière journée en attente de clôture :', state['day']['date'])
    print('Résultats :', args.output)


if __name__ == '__main__':
    main()
