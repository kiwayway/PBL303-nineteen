from flask import Blueprint, abort, render_template, request
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from ..models import Category, Review, Service, ServicePackage, User, OrderItem, Order
from ..auth.routes import current_user

bp = Blueprint("catalog", __name__)

@bp.get("/jasa")
def list_services():
    q = request.args.get("q", "").strip()[:100]
    category_id = request.args.get("kategori", type=int)
    sort = request.args.get("sort", "newest")
    query = Service.query.join(User, User.id == Service.seller_id).filter(
        Service.is_active.is_(True), User.is_active.is_(True)
    ).filter(Service.packages.any(ServicePackage.is_active.is_(True)))
    if q:
        query = query.filter(or_(Service.title.ilike(f"%{q}%"), Service.description.ilike(f"%{q}%")))
    if category_id:
        query = query.filter(Service.category_id == category_id)
    if sort == "title":
        query = query.order_by(Service.title.asc())
    else:
        query = query.order_by(Service.created_at.desc(), Service.id.desc())
    page = max(request.args.get("page", 1, type=int), 1)
    pagination = query.options(joinedload(Service.seller), joinedload(Service.category)).paginate(page=page, per_page=12, error_out=False)
    return render_template("catalog/list.html", user=current_user(), pagination=pagination, services=pagination.items,
        categories=Category.query.order_by(Category.name).all(), q=q, category_id=category_id, sort=sort)

@bp.get("/jasa/<int:service_id>")
def detail(service_id):
    service = Service.query.filter_by(id=service_id, is_active=True).first_or_404()
    if not service.seller.is_active:
        abort(404)
    packages = ServicePackage.query.filter_by(service_id=service.id, is_active=True).order_by(ServicePackage.price).all()
    reviews = (Review.query.join(OrderItem, Review.order_item_id == OrderItem.id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.status == "approved", OrderItem.status == "completed")
        .join(ServicePackage, ServicePackage.id == OrderItem.package_id)
        .filter(ServicePackage.service_id == service.id)
        .order_by(Review.created_at.desc()).limit(30).all())
    avg = service.avg_rating
    return render_template("catalog/detail.html", user=current_user(), service=service, packages=packages, reviews=reviews, average=avg)
