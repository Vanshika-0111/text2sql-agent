import pandas as pd
from sqlalchemy import create_engine, URL

# Connect to your database
url = URL.create(
    "postgresql",
    username="postgres",
    password="Azby#1928",   # put your real password here, # is fine
    host="localhost",
    port=5432,
    database="olist",
)
engine = create_engine(url)

# table name in database : CSV file name
files = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

for table, file in files.items():
    df = pd.read_csv("data/olist/" + file)

    # Turn date columns into real dates (so SQL can do date maths on them)
    for col in df.columns:
        if "date" in col or "timestamp" in col or col.endswith("_at"):
            df[col] = pd.to_datetime(df[col])

    df.to_sql(table, engine, if_exists="replace", index=False)
    print(table, "loaded:", len(df), "rows")