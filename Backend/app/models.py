from datetime import UTC, datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from app.database import Base


class VisitorLog(Base):
    __tablename__ = "visitor_logs"
    id = Column(Integer, primary_key=True)
    count = Column(Integer, default=20)


class Bus(Base):
    __tablename__ = "Buses"
    id = Column(Integer, primary_key=True)
    bus_name = Column(String, nullable=False)
    total_capacity = Column(Integer, default=20)
    status = Column(String, default="ACTIVE")
    seats = relationship("Seat", back_populates="bus", cascade="all,delete-orphan")
    views_count = Column(Integer, default=0, nullable=False)


class Seat(Base):
    __tablename__ = "seats"

    id = Column(Integer, primary_key=True, index=True)
    bus_id = Column(Integer, ForeignKey("Buses.id"), nullable=False)
    seat_number = Column(Integer, nullable=False)
    travel_date = Column(String, nullable=False)
    status = Column(String, default="AVAILABLE")
    held_by = Column(String, nullable=True)
    held_until = Column(DateTime(timezone=True), nullable=True)

    bus = relationship("Bus", back_populates="seats")


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    client_name = Column(String, nullable=False)
    seat_id = Column(Integer, ForeignKey("seats.id"), nullable=False)
    booking_time = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))