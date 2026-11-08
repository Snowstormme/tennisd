"""Complete moderator queues without hydrating unrelated histories or votes."""
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload
from . import db
from .models import Comment, FeedbackSubmission, ModerationAction, Poll, PollOption, PollVote, Report, Review
from .pagination import bounded_page
from .notifications import safe_notification_url


def moderation_context(*, ready, tab='reports', state='', category='', page=1):
    statements={
        'reports':select(Report).options(joinedload(Report.review).joinedload(Review.user),
            joinedload(Report.comment).joinedload(Comment.user),joinedload(Report.comment).joinedload(Comment.review))
            .order_by(Report.created_at,Report.id),
        'audit':select(ModerationAction).order_by(ModerationAction.created_at.desc(),ModerationAction.id.desc()),
    }
    if ready:
        statements.update(feedback=select(FeedbackSubmission).options(joinedload(FeedbackSubmission.user))
            .order_by(FeedbackSubmission.created_at.desc(),FeedbackSubmission.id.desc()),
            polls=select(Poll).order_by(Poll.created_at.desc(),Poll.id.desc()))
    if tab not in statements:tab='reports'
    totals={k:db.session.scalar(select(func.count()).select_from(v.order_by(None).subquery()))
            for k,v in statements.items()}
    state=state if state in (('open','dismissed','all') if tab=='reports' else ('new','reviewed','planned','closed','all')) else ('open' if tab=='reports' else 'all')
    category=category if tab=='feedback' and category in ('idea','bug','content','other') else ''
    statement=statements[tab]
    if tab in ('reports','feedback') and state!='all':
        statement=statement.where((Report if tab=='reports' else FeedbackSubmission).status==state)
    if category:statement=statement.where(FeedbackSubmission.category==category)
    pagination=bounded_page(statement,page=page,per_page=25)
    summary={'total':0,'average':None,'categories':[]}
    if ready and tab=='feedback':
        total,average=db.session.execute(select(func.count(FeedbackSubmission.id),func.avg(FeedbackSubmission.rating))).one()
        summary={'total':total,'average':average,'categories':db.session.execute(
            select(FeedbackSubmission.category,func.count()).group_by(FeedbackSubmission.category)
            .order_by(FeedbackSubmission.category)).all()}
    poll_results={}
    if tab=='polls' and pagination.items:
        rows=db.session.execute(select(PollOption.poll_id,PollOption.id,PollOption.label,
            func.count(PollVote.id).label('votes')).outerjoin(PollVote,PollVote.option_id==PollOption.id)
            .where(PollOption.poll_id.in_([p.id for p in pagination.items]))
            .group_by(PollOption.poll_id,PollOption.id,PollOption.label,PollOption.position)
            .order_by(PollOption.position,PollOption.id))
        for row in rows:poll_results.setdefault(row.poll_id,[]).append(row)
    return dict(tab=tab,state=state,category=category,totals=totals,pagination=pagination,
        reports=pagination.items if tab=='reports' else [],feedback_items=pagination.items if tab=='feedback' else [],
        polls=pagination.items if tab=='polls' else [],audit_actions=pagination.items if tab=='audit' else [],
        feedback_summary=summary,poll_results=poll_results,
        feedback_targets={item.id:safe_notification_url(item.page_path) for item in pagination.items} if tab=='feedback' else {})
