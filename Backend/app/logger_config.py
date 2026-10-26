import logging
import queue
from logging.handlers import QueueHandler,QueueListener

log_queue=queue.Queue(-1)
format=logging.Formatter(
    "%(levelname)s:%(message)s"
)

console_handler=logging.StreamHandler()
console_handler.setFormatter(format)

queue_listener=QueueListener(log_queue,console_handler)
queue_listener.start()
queue_handler=QueueHandler(log_queue)
logger=logging.getLogger("bus_booking_application")
logger.setLevel(logging.INFO)
logger.addHandler(queue_handler)

