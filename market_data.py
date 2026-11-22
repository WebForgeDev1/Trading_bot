
import pandas as pd


def load_market_data(file_path):
    df = pd.read_csv(file_path)

    required = [
        "timestamp", "open", "high", "low", "close"
    ]

    if not all(col in df.columns for col in required):
        raise ValueError("Colonnes CSV manquantes")

    # Timestamp Unix en millisecondes
    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True
    )

    df = df.sort_values("timestamp")
    df = df.drop_duplicates("timestamp")

    # Convertir les heures UTC en heure de Paris
    df = df.set_index("timestamp")
    df.index = df.index.tz_convert("Europe/Paris")

    return df


def build_daily_candles(df):
    daily = df.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last"
    })

    return daily.dropna()
