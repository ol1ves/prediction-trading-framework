from KalshiClient import KalshiClient
from config import load_config

def main():
    config = load_config()
    client = KalshiClient(config.kalshi)
    print(client.get_balance_cents())
    print(client.get_portfolio_value_cents())
    print(client.get_events())


if __name__ == "__main__":
    main()
