"""Bound tournament summaries in SQL before loading card metadata."""
from sqlalchemy import and_, case, func, literal, or_, select, true
from . import db
from .identities import catalog_is_visible
from .models import Match
from .pagination import Page, bounded_page
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
        .where(*conditions).group_by(Match.tour,Match.tournament).cte('tournament_summary')
    ordered=select(summary).order_by(summary.c.priority,summary.c.latest.desc(),summary.c.tournament,summary.c.tour)
    filtered=bool(query or tour or surface or level)
    featured_count=0
    featured=[]
    if not filtered:
        totals=select(
            func.count().filter(summary.c.priority!=0).label('ordinary_total'),
            func.count().filter(summary.c.priority==0).label('featured_count'),
        ).select_from(summary).cte('tournament_totals')
        columns=(summary.c.tour,summary.c.tournament,summary.c.matches,
                 summary.c.earliest,summary.c.latest,summary.c.priority)
        def fetch_directory_rows(requested_page):
            ordinary_rows=(select(literal(0).label('section_order'),*columns)
                .select_from(summary).where(summary.c.priority!=0)
                .order_by(summary.c.priority,summary.c.latest.desc(),summary.c.tournament,summary.c.tour)
                .limit(36).offset((requested_page-1)*36).cte('tournament_page_rows'))
            statements=[select(ordinary_rows)]
            if requested_page==1:
                featured_rows=(select(literal(1).label('section_order'),*columns)
                    .select_from(summary).where(summary.c.priority==0)
                    .order_by(summary.c.latest.desc(),summary.c.tournament,summary.c.tour)
                    .limit(8).cte('tournament_featured_rows'))
                statements.append(select(featured_rows))
            selected_rows=(statements[0].union_all(*statements[1:])
                           if len(statements)>1 else statements[0]).cte('tournament_rows')
            combined=totals.outerjoin(selected_rows,true())
            return db.session.execute(select(
                selected_rows.c.section_order,selected_rows.c.tour,selected_rows.c.tournament,
                selected_rows.c.matches,selected_rows.c.earliest,selected_rows.c.latest,
                selected_rows.c.priority,totals.c.ordinary_total,totals.c.featured_count,
            ).select_from(combined).order_by(
                selected_rows.c.section_order,selected_rows.c.priority,selected_rows.c.latest.desc(),
                selected_rows.c.tournament,selected_rows.c.tour,
            ).execution_options(tennisd_metric='tournament_directory')).all()

        requested_page=min(max(1,page),2_147_483_647)
        rows=fetch_directory_rows(requested_page)
        total=int(rows[0].ordinary_total or 0)
        featured_count=int(rows[0].featured_count or 0)
        pages=max(1,(total+35)//36)
        selected_page=min(requested_page,pages)
        if selected_page!=requested_page:
            rows=fetch_directory_rows(selected_page)
        page_items=[row for row in rows if row.section_order==0]
        featured=[row for row in rows if row.section_order==1]
        pagination=Page(items=page_items,total=total,page=selected_page,per_page=36)
    else:
        pagination=bounded_page(
            ordered,page=page,per_page=36,rows=True,
            count_metric="tournament_page_count",page_metric="tournament_page",
        )
    selected=list(pagination.items)+list(featured)
    metadata={}
    if selected:
        selection=or_(*(and_(Match.tour==r.tour,Match.tournament==r.tournament) for r in selected))
        metadata_statement = (
            select(Match.tour,Match.tournament,Match.surface,Match.level)
            .where(*conditions,selection).distinct()
            .execution_options(tennisd_metric="tournament_metadata")
        )
        for row in db.session.execute(metadata_statement):
            item=metadata.setdefault((row.tour,row.tournament),{'surfaces':set(),'levels':set()})
            item['surfaces'].add(row.surface);item['levels'].add(row.level)
    def cards(rows):
        result=[]
        for row in rows:
            item=dict(row._mapping);item.pop('section_order',None)
            item.update(metadata[(row.tour,row.tournament)])
            _,item['value_label'],item['value_group']=tournament_level(sorted(item['levels']))
            item.update(slug=tournament_slug(row.tournament),profile=tournament_profile(row.tournament))
            result.append(item)
        return result
    return pagination,cards(pagination.items),cards(featured),featured_count
