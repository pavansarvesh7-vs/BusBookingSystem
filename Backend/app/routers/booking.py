from datetime import UTC, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.logger_config import logger
from app.models import Bus, Seat, Ticket
from app.schemas import (
    BookingConfirmRequest,
    CancelSeatBooking,
    SeatAvailabilityResponse,
    SeatHoldRequest,
    SeatResponse,
)
from app.routers.admin import execute_current_strategy

router = APIRouter(tags=["Booking Operations"])


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


@router.get("/seats", response_model=SeatAvailabilityResponse)
def get_seats(
    bus_id: int,
    travel_date: str = Query(..., description="Format: YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    # Route execution through the Admin-selected serving strategy
    exec_info = execute_current_strategy(delay_seconds=1.5)

    db.expire_all()
    bus = db.query(Bus).filter(Bus.id == bus_id).first()
    if not bus:
        raise HTTPException(status_code=404, detail="No bus found")

    if bus.status == "ALTERATION_IN_PROGRESS":
        raise HTTPException(
            status_code=503,
            detail="Bus alteration in progress.",
        )

    bus.views_count += 1

    now = datetime.now(UTC)
    seats = (
        db.query(Seat)
        .filter(Seat.bus_id == bus.id, Seat.travel_date == travel_date)
        .all()
    )

    # Lazily release expired holds
    for seat in seats:
        held_until = _ensure_utc(seat.held_until)
        if seat.status == "HELD" and held_until and held_until < now:
            seat.status = "AVAILABLE"
            seat.held_until = None
            seat.held_by = None

    db.commit()

    logger.info(
        f"[/seats] Mode: {exec_info['strategy_used']} | "
        f"PID: {exec_info['pid']} | Thread: {exec_info['thread_name']} | "
        f"Time: {exec_info['elapsed_seconds']}s"
    )

    return {
        "bus_id": bus.id,
        "bus_name": bus.bus_name,
        "views_count": bus.views_count,
        "travel_date": travel_date,
        "seats": seats,
    }


@router.post("/hold")
def holdTheSeat(req: SeatHoldRequest, db: Session = Depends(get_db)):
    exec_info = execute_current_strategy(delay_seconds=1.5)

    db.expire_all()
    
    # Verify valid bus and seat boundary
    bus = db.query(Bus).filter(Bus.id == req.bus_id, Bus.status == "ACTIVE").first()
    if not bus:
        raise HTTPException(status_code=404, detail="Active bus not found")

    if req.seat_number < 1 or req.seat_number > bus.total_capacity:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid seat number. Bus capacity is {bus.total_capacity} seats."
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
        seat = (
            db.query(Seat).filter(Seat.id == seat.id).with_for_update().first()
        )

    if seat.status == "BOOKED":
        raise HTTPException(status_code=400, detail="Seat already booked")

    now = datetime.now(UTC)
    if seat.status == "HELD":
        held_until = _ensure_utc(seat.held_until)
        if held_until and held_until > now:
            if seat.held_by != req.held_by:
                raise HTTPException(
                    status_code=403,
                    detail=f"Seat is already held by another session ({seat.held_by})."
                )

    seat.status = "HELD"
    seat.held_by = req.held_by
    seat.held_until = now + timedelta(minutes=5)
    db.commit()

    logger.info(
        f"Seat {req.seat_number} on Bus {req.bus_id} held by {req.held_by} "
        f"(Mode: {exec_info['strategy_used']}, PID: {exec_info['pid']}, Thread: {exec_info['thread_name']})"
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
        held_until = _ensure_utc(seat.held_until)
        if held_until and held_until < now:
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
                detail=f"Access denied: Seat is held by a different session ({seat.held_by}).",
            )
    else:
        raise HTTPException(
            status_code=400, detail="Seat must be held before confirmation"
        )

    seat.status = "BOOKED"
    seat.held_by = req.client_name
    seat.held_until = None

    ticket = Ticket(
        client_name=req.client_name, seat_id=seat.id, booking_time=now
    )
    db.add(ticket)
    db.commit()

    logger.info(
        f"Ticket #{ticket.id} confirmed for {req.client_name} on Bus #{seat.bus_id} "
        f"(Mode: {exec_info['strategy_used']}, PID: {exec_info['pid']})"
    )

    # Auto-scaling Calculation at 80% Load Factor
    active_buses = db.query(Bus).filter(Bus.status == "ACTIVE").all()
    total_seats = sum(b.total_capacity for b in active_buses)

    total_bookings = (
        db.query(Seat)
        .filter(Seat.status == "BOOKED", Seat.travel_date == seat.travel_date)
        .count()
    )

    load_factor = total_bookings / total_seats if total_seats > 0 else 0

    scaled = False
    if load_factor >= 0.8:
        active_count = len(active_buses)
        max_bus = db.query(Bus).order_by(Bus.id.desc()).first()
        next_id = (max_bus.id if max_bus else 0) + 1

        new_bus1 = Bus(
            id=next_id,
            bus_name=f"B_{active_count + 1}",
            total_capacity=20,
            status="ACTIVE",
        )
        new_bus2 = Bus(
            id=next_id + 1,
            bus_name=f"B_{active_count + 2}",
            total_capacity=20,
            status="ACTIVE",
        )
        db.add_all([new_bus1, new_bus2])
        db.commit()

        scaled = True
        logger.info(
            f"Load factor at {load_factor:.2%}. Auto-scaling spawned Bus B_{active_count + 1} and B_{active_count + 2}."
        )

    return {
        "message": "Booking confirmed successfully",
        "ticket_id": ticket.id,
        "bus_id": seat.bus_id,
        "seat_number": seat.seat_number,
        "auto_scaled": scaled,
    }


@router.post("/cancel")
def cancelSeatBooking(req: CancelSeatBooking, db: Session = Depends(get_db)):
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
        raise HTTPException(
            status_code=404,
            detail="Seat not found for the specified bus and date",
        )

    if seat.status == "AVAILABLE":
        raise HTTPException(
            status_code=400,
            detail="Seat is already available.",
        )

    if seat.held_by and seat.held_by != req.client_name:
        raise HTTPException(
            status_code=403,
            detail="Access denied: You cannot cancel a seat held/booked by another customer.",
        )

    if seat.status == "BOOKED":
        ticket = (
            db.query(Ticket)
            .filter(
                Ticket.seat_id == seat.id, Ticket.client_name == req.client_name
            )
            .first()
        )
        if ticket:
            db.delete(ticket)

    seat.status = "AVAILABLE"
    seat.held_by = None
    seat.held_until = None
    db.commit()

    logger.info(
        f"Seat #{seat.seat_number} on Bus #{seat.bus_id} cancelled by {req.client_name} "
        f"(Mode: {exec_info['strategy_used']}, PID: {exec_info['pid']})"
    )

    return {
        "message": "Seat cancelled successfully",
        "status": seat.status,
    }


@router.get("/buses")
def list_buses(db: Session = Depends(get_db)):
    db.expire_all()

    buses = (
        db.query(Bus)
        .filter(Bus.status == "ACTIVE")
        .order_by(Bus.id.asc())
        .all()
    )

    return [
        {
            "id": b.id,
            "bus_name": f"B_{idx}",
            "total_capacity": b.total_capacity,
            "views_count": b.views_count,
            "status": b.status,
        }
        for idx, b in enumerate(buses, start=1)
    ]