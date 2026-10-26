from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import app.models
from app.database import Base, SessionLocal, engine
from app.models import Bus
from app.routers.admin import router as MergeBusesRouter
from app.routers.booking import router as OperationsOnBusesAndSeatsRouter

# ---------Create tables---------------------

Base.metadata.create_all(bind=engine)


def init_db():
    db = SessionLocal()
    try:
        if db.query(Bus).count() == 0:
            initial_buses = [
                Bus(id=i, bus_name=f"B_{i}", total_capacity=20, status="ACTIVE")
                for i in range(1, 11)
            ]
            db.add_all(initial_buses)
            db.commit()
    finally:
        db.close()
       
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Bus Booking System API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#  ------------Home page-----------------------
@app.get("/")
def home():
    return {"message": "Welcome to the Bus Booking Application API"}


#------Including the OperationsOnBusesAndSeatsRouter Into main.py-------

app.include_router(OperationsOnBusesAndSeatsRouter)

# ---Including the MergeBusesRouter into the main.py-----------------

app.include_router(MergeBusesRouter, prefix="/admin")