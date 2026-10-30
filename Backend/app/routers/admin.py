import multiprocessing
import os
import threading
import time
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.logger_config import logger
from app.models import Bus, Seat, Ticket
from app.schemas import MergeBusesRequest
from app.state import strategy_state

router = APIRouter()

last_iterative_finish = 0.0
iterative_lock = threading.Lock()


def _run_worker(delay, output_dict):
    output_dict["info"] = f"PID: {os.getpid()} | Thread: {threading.current_thread().name}"
    time.sleep(delay)

def execute_current_strategy(delay_seconds=1.5) -> dict:
    global last_iterative_finish
    start_ts = time.time()
    mode = strategy_state.current_strategy
    pid = os.getpid()
    thread_name = threading.current_thread().name
    if mode == "iterative":
        with iterative_lock:
            now = time.time()
            start_time = max(now, last_iterative_finish)
            last_iterative_finish = start_time + delay_seconds

        wait_time = start_time - time.time()
        if wait_time > 0:
            time.sleep(wait_time)

        logger.info(f"[/seats] [START] Mode: iterative | PID: {pid} | Thread: {thread_name}")
        time.sleep(delay_seconds)
        logger.info(f"[/seats] [FINISHED] Mode: iterative | PID: {pid} | Thread: {thread_name}")

    elif mode == "threaded":
        logger.info(f"[/seats] [START] Mode: threaded | PID: {pid} | Thread: {thread_name}")

        t = threading.Thread(target=time.sleep, args=(delay_seconds,))
        t.start()
        t.join()

        logger.info(f"[/seats] [FINISHED] Mode: threaded | PID: {pid} | Thread: {thread_name}")

    elif mode == "forking":
        logger.info(f"[/seats] [START] Mode: forking | PID: {pid} | Thread: {thread_name}")

        manager = multiprocessing.Manager()
        results = manager.dict()

        p = multiprocessing.Process(target=_run_worker, args=(delay_seconds, results))
        p.start()
        p.join()

        logger.info(f"[/seats] [FORKED PROCESS] -> {results.get('info', 'N/A')}")
        logger.info(f"[/seats] [FINISHED] Mode: forking | PID: {pid} | Thread: {thread_name}")

    return {
        "strategy_used": mode,
        "pid": pid,
        "thread_name": thread_name,
        "elapsed_seconds": round(time.time() - start_ts, 3),
    }


class StrategyRequest(BaseModel):
    strategy: str


@router.get("/strategy")
@router.get("/admin/strategy")
def get_serving_strategy():
    return {"active_strategy": strategy_state.current_strategy}


@router.post("/strategy")
@router.post("/admin/strategy")
def set_serving_strategy(req: StrategyRequest):
    global last_iterative_finish

    valid_strategies = {"iterative", "threaded", "forking"}
    if req.strategy not in valid_strategies:
        raise HTTPException(
            status_code=400,
            detail="Invalid strategy mode. Choose from 'iterative', 'threaded', or 'forking'.",
        )

    strategy_state.set_strategy(req.strategy)
    last_iterative_finish = 0.0

    logger.info(f"Execution serving strategy changed to: {req.strategy}")
    return {
        "message": f"Serving strategy updated to {req.strategy}",
        "active_strategy": req.strategy,
    }


@router.post("/merge")
@router.post("/admin/merge")
@router.post("/admin/buses/merge")
def merge_buses(req: MergeBusesRequest, db: Session = Depends(get_db)):
    if req.source_bus_id == req.target_bus_id:
        raise HTTPException(
            status_code=400,
            detail="Source and Target Bus IDs must be distinct.",
        )

    src_bus = db.query(Bus).filter(Bus.id == req.source_bus_id).first()
    tgt_bus = db.query(Bus).filter(Bus.id == req.target_bus_id).first()

    if not src_bus or not tgt_bus:
        raise HTTPException(
            status_code=404,
            detail=f"Bus not found (Source ID: {req.source_bus_id}, Target ID: {req.target_bus_id}).",
        )
    if src_bus.status != "ACTIVE" or tgt_bus.status != "ACTIVE":
        raise HTTPException(
            status_code=400,
            detail="Both Source and Target buses must have 'ACTIVE' status to merge.",
        )
    capacity = src_bus.total_capacity
    booked_count = (
        db.query(Seat)
        .filter(
            Seat.bus_id == src_bus.id,
            Seat.travel_date == req.travel_date,
            Seat.status == "BOOKED",
        )
        .count()
    )
    load_factor = booked_count / capacity if capacity > 0 else 0.0
    if load_factor >= 0.20:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot merge: Source Bus B_{src_bus.id} Load Factor is {load_factor:.1%} (must be < 20%).",
        )
    source_seats = (
        db.query(Seat)
        .filter(
            Seat.bus_id == src_bus.id,
            Seat.travel_date == req.travel_date,
            Seat.status.in_(["BOOKED", "HELD"]),
        )
        .order_by(Seat.seat_number.asc())
        .all()
    )
    target_seats = (
        db.query(Seat)
        .filter(Seat.bus_id == tgt_bus.id, Seat.travel_date == req.travel_date)
        .all()
    )
    if not target_seats:
        tgt_capacity = tgt_bus.total_capacity
        db.add_all([
            Seat(
                bus_id=tgt_bus.id,
                travel_date=req.travel_date,
                seat_number=sn,
                status="AVAILABLE",
            )
            for sn in range(1, tgt_capacity + 1)
        ])
        db.flush()
        target_seats = (
            db.query(Seat)
            .filter(Seat.bus_id == tgt_bus.id, Seat.travel_date == req.travel_date)
            .all()
        )

    target_map = {s.seat_number: s for s in target_seats}

    # Collision validation pass
    for seat in source_seats:
        tgt_seat = target_map.get(seat.seat_number)
        if tgt_seat and tgt_seat.status != "AVAILABLE":
            raise HTTPException(
                status_code=400,
                detail=f"Not possible due to collision: Seat #{seat.seat_number} is already occupied/held on Target Bus B_{tgt_bus.id}.",
            )
    for seat in source_seats:
        dest_seat = target_map.get(seat.seat_number)
        if not dest_seat:
            dest_seat = Seat(
                bus_id=tgt_bus.id,
                seat_number=seat.seat_number,
                travel_date=req.travel_date,
            )
            db.add(dest_seat)
            db.flush()
        dest_seat.status = seat.status
        dest_seat.held_by = seat.held_by
        dest_seat.held_until = seat.held_until
        db.flush()
        tickets = db.query(Ticket).filter(Ticket.seat_id == seat.id).all()
        for t in tickets:
            t.seat_id = dest_seat.id
        seat.status = "AVAILABLE"
        seat.held_by = None
        seat.held_until = None
    src_bus.status = "ALTERATION_IN_PROGRESS"
    db.commit()
    logger.info(f"Merged Bus #{src_bus.id} into Bus #{tgt_bus.id} for date {req.travel_date}")
    return {
        "message": f"Successfully merged Bus B_{src_bus.id} into Bus B_{tgt_bus.id}",
        "source_bus_id": src_bus.id,
        "target_bus_id": tgt_bus.id,
    }
@router.post("/set-fleet")
@router.post("/admin/set-fleet")
@router.post("/admin/buses/set-fleet")
def set_fleet_size(count: int = Query(..., ge=1, le=100), db: Session = Depends(get_db)):
    for i in range(1, count + 1):
        bus = db.query(Bus).filter(Bus.id == i).first()
        if bus:
            bus.status = "ACTIVE"
            bus.bus_name = f"B_{i}"
        else:
            db.add(Bus(id=i, bus_name=f"B_{i}", total_capacity=20, status="ACTIVE"))

    surplus = db.query(Bus).filter(Bus.id > count).all()
    for bus in surplus:
        bus.status = "INACTIVE"

    db.commit()
    return {"message": f"Fleet size updated to {count} active buses (B_1 to B_{count})."}