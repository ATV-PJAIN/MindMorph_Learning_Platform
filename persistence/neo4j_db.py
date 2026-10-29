"""Neo4j driver — lazy singleton, mirrors the pattern in persistence/db.py."""
import os
from typing import Optional
from neo4j import GraphDatabase, Driver

_driver: Optional[Driver] = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        uri      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
        user     = os.getenv("NEO4J_USER",     "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "mindmorph_neo4j")
        _driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=3,
                                       connection_acquisition_timeout=5, max_transaction_retry_time=3)
    return _driver


def close_driver():
    global _driver
    if _driver:
        _driver.close()
        _driver = None
