import os
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.logger_config import logger
from app.models import Bus, Seat, Ticket
from app.routers.admin import execute_current_strategy
from app.schemas import (
    BookingConfirmRequest,
    CancelSeatBooking,
    SeatAvailabilityResponse,
    SeatHoldRequest,
)

router = APIRouter()


def parse_utc(dt: datetime | None) -> datetime | None:
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@router.get("/seats", response_model=SeatAvailabilityResponse)
def get_seats(
    bus_id: int,
    travel_date: str = Query(..., description="Format: YYYY-MM-DD"),
    is_poll: bool = Query(False, description="Whether request is a background poll"),
    db: Session = Depends(get_db),
):
    if not is_poll:
        exec_info = execute_current_strategy(delay_seconds=1.5)
    else:
        exec_info = {"strategy_used": "polling_skipped", "pid": os.getpid(), "thread_name": "poll"}

    db.expire_all()

    bus = db.query(Bus).filter(Bus.id == bus_id).first()
    if not bus:
        raise HTTPException(status_code=404, detail="No bus found")

    if bus.status == "ALTERATION_IN_PROGRESS":
        raise HTTPException(status_code=503, detail="Bus alteration in progress.")

    if not is_poll:
        bus.views_count = (bus.views_count or 0) + 1
        db.commit()
    now = datetime.now(UTC)
    seats = (
        db.query(Seat)
        .filter(Seat.bus_id == bus.id, Seat.travel_date == travel_date)
        .order_by(Seat.seat_number.asc())
        .all()
    )
    for s in seats:
        expiry = parse_utc(s.held_until)
        if s.status == "HELD" and expiry and expiry < now:
            s.status = "AVAILABLE"
            s.held_until = None
            s.held_by = None

    db.commit()

    if not is_poll:
        logger.info(
            f"[/seats] Mode: {exec_info['strategy_used']} | PID: {exec_info['pid']} | Thread: {exec_info['thread_name']}"
        )

    return {
        "bus_id": bus.id,
        "bus_name": bus.bus_name or f"B_{bus.id}",
        "views_count": bus.views_count or 0,
        "travel_date": travel_date,
        "seats": seats,
    }
@router.post("/hold")
def hold_seat(req: SeatHoldRequest, db: Session = Depends(get_db)):
    exec_info = execute_current_strategy(delay_seconds=1.5)
    db.expire_all()
    bus = db.query(Bus).filter(Bus.id == req.bus_id, Bus.status == "ACTIVE").first()
    if not bus:
        raise HTTPException(status_code=404, detail="Active bus not found")
    if req.seat_number < 1 or req.seat_number > bus.total_capacity:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid seat number. Capacity is {bus.total_capacity}.",
        )
    seat = (
        db.query(Seat)
        .filter(
            Seat.bus_id == req.bus_id,
            Seat.seat_number == req.seat_number,
            Seat.travel_date == req.travel_date,
        )
        .with_for_update()
        .first()
    )
    if not seat:
        seat = Seat(
            bus_id=req.bus_id,
            seat_number=req.seat_number,
            travel_date=req.travel_date,
            status="AVAILABLE",
        )
        db.add(seat)
        db.flush()
    if seat.status == "BOOKED":
        raise HTTPException(status_code=400, detail="Seat already booked")
    now = datetime.now(UTC)
    if seat.status == "HELD":
        expiry = parse_utc(seat.held_until)
        if expiry and expiry > now and seat.held_by != req.held_by:
            raise HTTPException(
                status_code=403,
                detail=f"Seat currently held by session {seat.held_by}.",
            )
    seat.status = "HELD"
    seat.held_by = req.held_by
    seat.held_until = now + timedelta(minutes=5)
    db.commit()
    logger.info(
        f"Seat {req.seat_number} on Bus {req.bus_id} held by {req.held_by} (Mode: {exec_info['strategy_used']})"
    )
    return {
        "message": "Seat held successfully",
        "seat_id": seat.id,
        "held_until": seat.held_until,
    }
@router.post("/confirm")
def confirm_booking(req: BookingConfirmRequest, db: Session = Depends(get_db)):
    exec_info = execute_current_strategy(delay_seconds=1.5)
    db.expire_all()
    seat = (
        db.query(Seat)
        .filter(
            Seat.bus_id == req.bus_id,
            Seat.seat_number == req.seat_number,
            Seat.travel_date == req.travel_date,
        )
        .with_for_update()
        .first()
    )
    if not seat:
        raise HTTPException(status_code=404, detail="Seat record not found")
    now = datetime.now(UTC)
    if seat.status == "BOOKED":
        raise HTTPException(status_code=400, detail="Seat already booked")
    if seat.status == "HELD":
        expiry = parse_utc(seat.held_until)
        if expiry and expiry < now:
            seat.status = "AVAILABLE"
            seat.held_by = None
            seat.held_until = None
            db.commit()
            raise HTTPException(
                status_code=400,
                detail="Seat hold has expired. Please hold the seat again.",
            )
        if seat.held_by != req.client_name:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied: Seat is held by another session ({seat.held_by}).",
            )
    else:
        raise HTTPException(status_code=400, detail="Seat must be held before confirmation")
    seat.status = "BOOKED"
    seat.held_by = req.client_name
    seat.held_until = None
    ticket = Ticket(client_name=req.client_name, seat_id=seat.id, booking_time=now)
    db.add(ticket)
    db.commit()
    logger.info(
        f"Ticket #{ticket.id} confirmed for {req.client_name} on Bus #{seat.bus_id} (Mode: {exec_info['strategy_used']})"
    )
    active_buses = db.query(Bus).filter(Bus.status == "ACTIVE").all()
    total_capacity = sum(b.total_capacity for b in active_buses)
    total_booked = (
        db.query(Seat)
        .filter(Seat.status == "BOOKED", Seat.travel_date == seat.travel_date)
        .count()
    )
    load_factor = total_booked / total_capacity if total_capacity > 0 else 0.0
    scaled = False
    if load_factor >= 0.8:
        max_bus = db.query(Bus).order_by(Bus.id.desc()).first()
        next_id = (max_bus.id if max_bus else 0) + 1

        db.add_all([
            Bus(id=next_id, bus_name=f"B_{next_id}", total_capacity=20, status="ACTIVE"),
            Bus(id=next_id + 1, bus_name=f"B_{next_id + 1}", total_capacity=20, status="ACTIVE"),
        ])
        db.commit()
        scaled = True
        logger.info(f"High load detected ({load_factor:.1%}). Added buses B_{next_id} and B_{next_id + 1}.")

    return {
        "message": "Booking confirmed successfully",
        "ticket_id": ticket.id,
        "bus_id": seat.bus_id,
        "seat_number": seat.seat_number,
        "auto_scaled": scaled,
    }


@router.post("/cancel")
def cancel_booking(req: CancelSeatBooking, db: Session = Depends(get_db)):
    exec_info = execute_current_strategy(delay_seconds=1.5)
    db.expire_all()

    seat = (
        db.query(Seat)
        .filter(
            Seat.bus_id == req.bus_id,
            Seat.seat_number == req.seat_number,
            Seat.travel_date == req.travel_date,
        )
        .with_for_update()
        .first()
    )

    if not seat:
        raise HTTPException(status_code=404, detail="Seat not found")

    if seat.status == "AVAILABLE":
        raise HTTPException(status_code=400, detail="Seat is already available.")

    if seat.held_by and seat.held_by != req.client_name:
        raise HTTPException(
            status_code=403,
            detail="Access denied: Cannot cancel another customer's hold or booking.",
        )

    if seat.status == "BOOKED":
        ticket = (
            db.query(Ticket)
            .filter(Ticket.seat_id == seat.id, Ticket.client_name == req.client_name)
            .first()
        )
        if ticket:
            db.delete(ticket)

    seat.status = "AVAILABLE"
    seat.held_by = None
    seat.held_until = None
    db.commit()

    logger.info(
        f"Seat #{seat.seat_number} on Bus #{seat.bus_id} cancelled by {req.client_name} (Mode: {exec_info['strategy_used']})"
    )

    return {"message": "Seat cancelled successfully", "status": seat.status}


@router.get("/buses")
def list_buses(db: Session = Depends(get_db)):
    # CRITICAL FIX: DO NOT call execute_current_strategy here!
    db.expire_all()

    buses = db.query(Bus).filter(Bus.status == "ACTIVE").order_by(Bus.id.asc()).all()
    return [
        {
            "id": b.id,
            "bus_name": b.bus_name or f"B_{b.id}",
            "total_capacity": b.total_capacity,
            "views_count": b.views_count or 0,
            "status": b.status,
        }
        for b in buses
    ]