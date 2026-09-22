from sqlalchemy import create_engine, text
engine = create_engine("postgresql://krusch:kruschpassword@127.0.0.1:5432/krusch_nexus_db")
with engine.connect() as conn:
    res = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))
    for row in res:
        print(row)
