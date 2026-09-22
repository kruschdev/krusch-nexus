from sqlalchemy import create_engine, text
engine = create_engine("postgresql://openclaw:openclaw_password@10.0.0.85:5434/kruschdb")
with engine.connect() as conn:
    res = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'blobs'"))
    for row in res:
        print(row)
