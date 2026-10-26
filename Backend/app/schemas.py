from pydantic import BaseModel
from typing import Optional,List
from datetime import datetime

# -------requests for holding the seats-------

class SeatHoldRequest(BaseModel):
    held_by:str
    bus_id:int
    seat_number:int
    travel_date:str

# --------Booking Confirmation ------------

class BookingConfirmRequest(BaseModel):
    client_name: str
    bus_id: int
    seat_number: int
    travel_date: str

# --------Merging Buses at the time of lf less than 0.2--------

class MergeBusesRequest(BaseModel):
    source_bus_id:int
    target_bus_id:int
    travel_date:str

class MergeBusRequest(BaseModel):
    source_bus_id:int
    target_bus_id:int
    travel_date:str


# ---------Seat Data Fetched from database-------------------

class SeatResponse(BaseModel):
    id: int
    bus_id: int
    seat_number: int
    travel_date: str
    status: str
    held_by: str | None = None
    held_until: datetime | None = None

    class Config:
        from_attributes = True

# ----------Fetching the data about the bus ------------

class SeatAvailabilityResponse(BaseModel):
    bus_id: int
    bus_name: str
    views_count: int
    travel_date: str
    seats: list[SeatResponse]

    class Config:
        from_attributes = True

# ---------Bus Data Fetched from database-----------------------

class BusResponse(BaseModel):
    id:int
    bus_name:str
    total_capacity:int
    status:str
    class Config:
        from_attributes=True

# -------Seat Cancellation-------------------------------------

class CancelSeatBooking(BaseModel):
    client_name:str
    bus_id:int
    seat_number:int
    travel_date:str
