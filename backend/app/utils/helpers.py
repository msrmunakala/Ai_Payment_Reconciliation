from datetime import datetime


def format_currency(amount: float, currency: str = "USD") -> str:
    symbols = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£", "KES": "KSh"}
    symbol = symbols.get(currency.upper(), f"{currency} ")
    return f"{symbol}{amount:,.2f}"


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()
