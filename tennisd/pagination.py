"""Count and bound a collection before calculating its database offset."""

from dataclasses import dataclass

from sqlalchemy import func, select

from . import db


@dataclass(frozen=True)
class Page:
    items: list
    total: int
    page: int
    per_page: int

    @property
    def pages(self):
        return max(1, (self.total + self.per_page - 1) // self.per_page)

    @property
    def has_prev(self):
        return self.page > 1

    @property
    def has_next(self):
        return self.page < self.pages

    @property
    def prev_num(self):
        return self.page - 1 if self.has_prev else None

    @property
    def next_num(self):
        return self.page + 1 if self.has_next else None


def bounded_page(statement, *, page=1, per_page=18, rows=False):
    """Ignore oversized page requests before they can overflow a SQL offset."""
    if not 1 <= per_page <= 50:
        raise ValueError("Page size must be between 1 and 50.")
    total = db.session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), pages)
    query = statement.limit(per_page).offset((page - 1) * per_page)
    items = db.session.execute(query).all() if rows else db.session.scalars(query).all()
    return Page(items=items, total=total, page=page, per_page=per_page)
