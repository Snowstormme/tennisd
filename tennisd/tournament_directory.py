"""Bound tournament summaries in SQL before loading card metadata."""
from sqlalchemy import and_, case, func, or_, select
from . import db
from .identities import catalog_is_visible
from .models import Match
from .pagination import bounded_page
from .search_queries import contains_text
from .tournament_catalog import LEVEL_DETAILS, tournament_level, tournament_profile, tournament_slug


def tournament_directory(*, query='', tour='', surface='', level='', page=1):
    conditions=[catalog_is_visible()]
    if query:conditions.append(contains_text(Match.tournament,query))
    if tour:conditions.append(Match.tour==tour)
    if surface:conditions.append(Match.surface==surface)
    if level:conditions.append(Match.level==level)
    priority=case({key:value[0] for key,value in LEVEL_DETAILS.items()},value=Match.level,else_=9)
    summary=select(Match.tour,Match.tournament,
        func.count(Match.id).label('matches'),func.min(Match.week_start).label('earliest'),
        func.max(Match.week_start).label('latest'),func.min(priority).label('priority'))\
        .where(*conditions).group_by(Match.tour,Match.tournament).subquery()
    ordered=select(summary).order_by(summary.c.priority,summary.c.latest.desc(),summary.c.tournament,summary.c.tour)
    filtered=bool(query or tour or surface or level)
    featured_count=0
    featured=[]
    if not filtered:
        featured_count=db.session.scalar(select(func.count()).select_from(summary).where(summary.c.priority==0))
        ordered=ordered.where(summary.c.priority!=0)
    pagination=bounded_page(ordered,page=page,per_page=36,rows=True)
    # Show the bounded major preview only on the first unfiltered page. The
    # dedicated level filter remains the complete archive of major events.
    if not filtered and pagination.page==1:
        featured=db.session.execute(select(summary).where(summary.c.priority==0)
            .order_by(summary.c.latest.desc(),summary.c.tournament,summary.c.tour).limit(8)).all()
    selected=list(pagination.items)+list(featured)
    metadata={}
    if selected:
        selection=or_(*(and_(Match.tour==r.tour,Match.tournament==r.tournament) for r in selected))
        for row in db.session.execute(select(Match.tour,Match.tournament,Match.surface,Match.level)
            .where(*conditions,selection).distinct()):
            item=metadata.setdefault((row.tour,row.tournament),{'surfaces':set(),'levels':set()})
            item['surfaces'].add(row.surface);item['levels'].add(row.level)
    def cards(rows):
        result=[]
        for row in rows:
            item=dict(row._mapping);item.update(metadata[(row.tour,row.tournament)])
            _,item['value_label'],item['value_group']=tournament_level(sorted(item['levels']))
            item.update(slug=tournament_slug(row.tournament),profile=tournament_profile(row.tournament))
            result.append(item)
        return result
    return pagination,cards(pagination.items),cards(featured),featured_count
