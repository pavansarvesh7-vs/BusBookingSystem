from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.logger_config import logger
from app.models import Bus, Seat, Ticket
from app.schemas import MergeBusesRequest
from pydantic import BaseModel

router = APIRouter()


@router.post("/buses/merge")
def mergeBuses(req: MergeBusesRequest, db: Session = Depends(get_db)):
    db.expire_all()

    source_bus = db.query(Bus).filter(Bus.id == req.source_bus_id).first()
    target_bus = db.query(Bus).filter(Bus.id == req.target_bus_id).first()

    if not source_bus or not target_bus:
        raise HTTPException(
            status_code=404, detail="Source or target bus not found"
        )

    if source_bus.id == target_bus.id:
        raise HTTPException(
            status_code=400, detail="Source and target bus cannot be the same"
        )

    # Standardized date string conversion
    travel_date_str = str(req.travel_date)

    source_seats = (
        db.query(Seat)
        .filter(
            Seat.travel_date == travel_date_str,
            Seat.bus_id == source_bus.id,
            Seat.status.in_(["BOOKED", "HELD"]),
        )
        .all()
    )

    target_seats = (
        db.query(Seat)
        .filter(
            Seat.travel_date == travel_date_str,
            Seat.bus_id == target_bus.id,
            Seat.status.in_(["BOOKED", "HELD"]),
        )
        .all()
    )

    total_capacity = source_bus.total_capacity
    lf1 = len(source_seats) / total_capacity if total_capacity > 0 else 0
    lf2 = len(target_seats) / total_capacity if total_capacity > 0 else 0

    if lf1 >= 0.2 and lf2 >= 0.2:
        raise HTTPException(
            status_code=400,
            detail="Load factor for both buses exceeds 20%. Merge not permitted.",
        )

    source_seat_numbers = {s.seat_number for s in source_seats}
    target_seat_numbers = {s.seat_number for s in target_seats}

    # Strict Zero-Collision Rule
    conflicts = source_seat_numbers.intersection(target_seat_numbers)
    if conflicts:
        raise HTTPException(
            status_code=400,
            detail=f"Seat collision detected on seat(s) {sorted(list(conflicts))}. Merge aborted.",
        )

    # Lock buses during mutation
    source_bus.status = "ALTERATION_IN_PROGRESS"
    target_bus.status = "ALTERATION_IN_PROGRESS"
    db.commit()

    try:
        # Fetch all existing seats on target bus for this date to safely merge
        all_target_seats = (
            db.query(Seat)
            .filter(
                Seat.travel_date == travel_date_str,
                Seat.bus_id == target_bus.id,
            )
            .all()
        )
        target_seat_map = {s.seat_number: s for s in all_target_seats}

        for s_seat in source_seats:
            num = s_seat.seat_number

            if num in target_seat_map:
                # Copy status and hold metadata to the target bus seat record
                t_seat = target_seat_map[num]
                t_seat.status = s_seat.status
                t_seat.held_by = s_seat.held_by
                t_seat.held_until = s_seat.held_until

                # Re-link any issued tickets to the target seat
                if s_seat.status == "BOOKED":
                    ticket = (
                        db.query(Ticket)
                        .filter(Ticket.seat_id == s_seat.id)
                        .first()
                    )
                    if ticket:
                        ticket.seat_id = t_seat.id

                # Safely purge original source seat entry
                db.delete(s_seat)
            else:
                # Move seat record directly if no placeholder existed
                s_seat.bus_id = target_bus.id

        source_bus.status = "MERGED"
        target_bus.status = "ACTIVE"
        db.commit()

        logger.info(
            f"Successfully merged Bus #{source_bus.id} into Bus #{target_bus.id} for date {travel_date_str}"
        )

        return {
            "message": "Merge operation completed successfully",
            "source_bus_id": source_bus.id,
            "target_bus_id": target_bus.id,
            "migrated_seats_count": len(source_seats),
        }

    except Exception as e:
        db.rollback()
        source_bus.status = "ACTIVE"
        target_bus.status = "ACTIVE"
        db.commit()

        logger.error(f"Merge operation failed: {e}")
        raise HTTPException(status_code=500, detail="Merge operation failed")


@router.post("/buses/set-fleet")
def set_fleet_size(
    count: int = Query(..., ge=1, le=100), db: Session = Depends(get_db)
):
    db.expire_all()

    buses = (
        db.query(Bus)
        .filter(Bus.status != "MERGED")
        .order_by(Bus.id.asc())
        .all()
    )
    existing_count = len(buses)

    if count <= existing_count:
        for index, bus in enumerate(buses, start=1):
            bus.bus_name = f"B_{index}"
            if index <= count:
                bus.status = "ACTIVE"
            else:
                bus.status = "INACTIVE"
    else:
        for index, bus in enumerate(buses, start=1):
            bus.bus_name = f"B_{index}"
            bus.status = "ACTIVE"

        needed = count - existing_count
        max_bus = db.query(Bus).order_by(Bus.id.desc()).first()
        next_id = (max_bus.id if max_bus else 0) + 1

        new_buses = []
        for i in range(needed):
            bus_num = existing_count + 1 + i
            db_id = next_id + i
            new_buses.append(
                Bus(
                    id=db_id,
                    bus_name=f"B_{bus_num}",
                    total_capacity=20,
                    status="ACTIVE",
                )
            )
        db.add_all(new_buses)

    db.commit()
    return {"message": "Number of buses updated successfully!"}


class StrategyRequest(BaseModel):
    strategy: str  # "iterative", "threaded", or "forking"


CURRENT_SERVED_STRATEGY = "threaded"


@router.post("/strategy")
def set_serving_strategy(req: StrategyRequest):
    global CURRENT_SERVED_STRATEGY
    if req.strategy not in ["iterative", "threaded", "forking"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid strategy mode. Choose from 'iterative', 'threaded', or 'forking'.",
        )
    CURRENT_SERVED_STRATEGY = req.strategy
    logger.info(
        f"Execution serving strategy changed to: {CURRENT_SERVED_STRATEGY}"
    )
    return {
        "message": f"Serving strategy updated to {CURRENT_SERVED_STRATEGY}",
        "active_strategy": CURRENT_SERVED_STRATEGY,
    }