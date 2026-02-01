from KalshiClient import KalshiClient
from config import load_config

def main():
    config = load_config()
    kalshi = KalshiClient(config.kalshi)
    event = kalshi.get_event("KXPGATOUR-faio26")
    markets = [market for market in event['markets']]
    
    order = kalshi.create_order(markets[0]['ticker'], 'yes', 'buy', 1, markets[0]['yes_bid'])
    print(kalshi.get_order(order['order_id']))

if __name__ == "__main__":
    main()
