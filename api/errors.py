"""Errors the routes raise; investigation.py turns them into the contract's Problem responses."""


class NotFound(Exception): ...


class NotReviewable(Exception): ...
