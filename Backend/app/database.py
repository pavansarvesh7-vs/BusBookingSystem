# ----------import statements----------- 
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker,declarative_base

# ----------DatabaseUrl-------------------
DataBase_url="postgresql+psycopg://postgres:postgres@localhost:5432/bus_booking_db"

#--------------core sqlalchemy engine connected to postgresql-----------  
engine=create_engine(
    DataBase_url,
    pool_size=20,
    max_overflow=10
)

# ---------A session factory object which is used to instantiate the database session for every http request------
SessionLocal=sessionmaker(autocommit=False,autoflush=False,bind=engine)

# ----- creating a base class so the model classes can be inherit from this Base class------
Base=declarative_base()

# ------ creating a function so that data session can be instantiated for every http request-------
def get_db():
    db=SessionLocal()
    try:
        yield db
    finally:
        db.close()

