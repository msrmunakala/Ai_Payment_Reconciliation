from sqlalchemy.orm import Session
from app.models.reconciliation import FXRate

# Default exchange rates relative to 1 USD
DEFAULT_RATES = {
    "USD": 1.0,
    "INR": 83.50,
    "EUR": 0.92,
    "GBP": 0.78,
    "KES": 129.50,
    "TZS": 2680.0,
    "UGX": 3720.0,
}


def init_fx_rates(db: Session):
    for c_code, rate in DEFAULT_RATES.items():
        existing = db.query(FXRate).filter(
            FXRate.from_currency == c_code,
            FXRate.to_currency == "USD"
        ).first()
        if not existing:
            db.add(FXRate(from_currency=c_code, to_currency="USD", rate=1.0 / rate))
    db.commit()


def convert_to_usd(amount: float, currency: str, db: Session = None) -> float:
    c_upper = currency.upper() if currency else "USD"
    if c_upper == "USD":
        return amount
    
    rate = DEFAULT_RATES.get(c_upper)
    if rate:
        return amount / rate
    return amount
