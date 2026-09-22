from sqlalchemy import create_engine, inspect
engine = create_engine("postgresql://krusch:kruschpassword@db:5432/krusch_oss_db")
insp = inspect(engine)
columns = insp.get_columns("data_workspace_documents_vectors_bge")
for col in columns:
    print(col["name"], col["type"])
