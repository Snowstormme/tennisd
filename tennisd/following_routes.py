"""Private, complete follow lists and owner-scoped explicit removal actions."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import delete, func, select
from sqlalchemy.orm import joinedload
from . import db
from .models import FollowedPlayer, Player, TournamentSubscription
from .pagination import bounded_page
from .search_queries import contains_text
from .web import require_database_id, schema_table_exists


def follow_totals(user_id):
    result={'players':db.session.scalar(select(func.count(FollowedPlayer.id)).where(FollowedPlayer.user_id==user_id))}
    if schema_table_exists(TournamentSubscription.__tablename__):
        result['tournaments']=db.session.scalar(select(func.count(TournamentSubscription.id)).where(TournamentSubscription.user_id==user_id))
    return result


def register_following_routes(site, *, tournament_slug):
    @site.get('/settings/following')
    @login_required
    def following():
        totals=follow_totals(current_user.id)
        tab=request.args.get('tab','players')
        if tab not in totals:tab='players'
        query=request.args.get('q','').strip()[:80]
        tour=request.args.get('tour','')
        if tour not in ('ATP','WTA'):tour=''
        if tab=='players':
            statement=select(FollowedPlayer).join(Player).where(FollowedPlayer.user_id==current_user.id)\
                .options(joinedload(FollowedPlayer.player)).order_by(FollowedPlayer.id.desc())
            if query:statement=statement.where(contains_text(Player.name,query))
            if tour:statement=statement.where(Player.tour==tour)
        else:
            statement=select(TournamentSubscription).where(TournamentSubscription.user_id==current_user.id)\
                .order_by(TournamentSubscription.created_at.desc(),TournamentSubscription.id.desc())
            if query:statement=statement.where(contains_text(TournamentSubscription.tournament,query))
            if tour:statement=statement.where(TournamentSubscription.tour==tour)
        pagination=bounded_page(statement,page=request.args.get('page',1,type=int),per_page=30)
        return render_template('following.html',tab=tab,query=query,tour=tour,totals=totals,
            pagination=pagination,tournament_slug=tournament_slug)

    @site.post('/settings/following/<kind>/<int:follow_id>/remove')
    @login_required
    def remove_following(kind,follow_id):
        follow_id=require_database_id(follow_id)
        if kind=='players':model=FollowedPlayer
        elif kind=='tournaments' and schema_table_exists(TournamentSubscription.__tablename__):model=TournamentSubscription
        else:abort(404)
        result=db.session.execute(delete(model).where(model.id==follow_id,model.user_id==current_user.id))
        if result.rowcount!=1:abort(404)
        db.session.commit()
        flash('Subscription removed.','success')
        tour=request.form.get('tour','')
        return redirect(url_for('site.following',tab=kind,q=request.form.get('q','').strip()[:80],
            tour=tour if tour in ('ATP','WTA') else '',page=request.form.get('page',1,type=int),_anchor='results'))
