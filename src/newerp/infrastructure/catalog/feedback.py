"""Reviews (dd_review) with their photos (dd_review_image) and customer questions (dd_question) with product names."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    case,
    delete,
    func,
    select,
)
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.catalog.feedback import (
    FeedbackState,
    QuestionDto,
    ReviewDto,
    ReviewImageDto,
    ReviewImageStatus,
    UpdateQuestionRequest,
    UpdateReviewRequest,
)
from newerp.application.common import PagedResult
from newerp.infrastructure.models.catalog import DdProductDescription

metadata = MetaData()
review = Table(
    "dd_review",
    metadata,
    Column("review_id", Integer, primary_key=True),
    Column("product_id", Integer),
    Column("customer_id", Integer),
    Column("author", String(64)),
    Column("text", Text),
    Column("rating", Integer),
    Column("status", Boolean),
    Column("date_added", DateTime),
    Column("date_modified", DateTime),
)
review_image = Table(
    "dd_review_image",
    metadata,
    Column("image_id", Integer, primary_key=True),
    Column("review_id", Integer),
    Column("image", String(255)),
    Column("sort_order", Integer),
    Column("status", Integer),
    Column("date_added", DateTime),
)
# The old site keeps the photo status as a number.
IMAGE_STATUSES: dict[int, ReviewImageStatus] = {0: "pending", 1: "published", 2: "rejected"}
IMAGE_CODES = {status: code for code, status in IMAGE_STATUSES.items()}
question = Table(
    "dd_question",
    metadata,
    Column("question_id", Integer, primary_key=True),
    Column("product_id", Integer),
    Column("name", String(64)),
    Column("telephone", String(32)),
    Column("city", String(128)),
    Column("question", Text),
    Column("comment", Text),
    Column("status", Boolean),
    Column("date_question", Date),
    Column("date_added", DateTime),
)


def _real(column):
    """Zero dates of the old site come back empty."""
    return case((column < "1000-01-02", None), else_=column)


class SqlFeedbackStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    def _product_name(self, product_id):
        d = DdProductDescription
        return select(d.name).where(d.product_id == product_id, d.language_id == self._language_id).scalar_subquery()

    async def reviews(self, state: FeedbackState, page: int, page_size: int) -> PagedResult[ReviewDto]:
        r = review.c
        query = select(r.review_id)
        if state != "all":
            query = query.where(r.status.is_(state == "published"))
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self._session.execute(
            query.with_only_columns(
                r.review_id,
                r.product_id,
                func.coalesce(self._product_name(r.product_id), ""),
                r.author,
                r.text,
                r.rating,
                r.status,
                _real(r.date_added),
            )
            .order_by(r.review_id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            ReviewDto(
                id=x[0],
                product_id=x[1],
                product_name=x[2],
                author=x[3],
                text=x[4],
                rating=x[5],
                published=x[6],
                date_added=x[7] or datetime(2000, 1, 1),
            )
            for x in rows
        ]
        images = await self._images([item.id for item in items])
        items = [item.model_copy(update={"images": images.get(item.id, [])}) for item in items]
        return PagedResult[ReviewDto](items=items, page=page, page_size=page_size, total_count=total or 0)

    async def _images(self, review_ids: list[int]) -> dict[int, list[ReviewImageDto]]:
        """Every photo of the page's reviews, whatever its status, in the storefront's order."""
        if not review_ids:
            return {}
        i = review_image.c
        rows = await self._session.execute(
            select(i.review_id, i.image_id, i.image, i.status)
            .where(i.review_id.in_(review_ids))
            .order_by(i.sort_order, i.image_id)
        )
        images: dict[int, list[ReviewImageDto]] = {}
        for review_id, image_id, path, status in rows:
            dto = ReviewImageDto(id=image_id, path=path, status=IMAGE_STATUSES.get(status, "pending"))
            images.setdefault(review_id, []).append(dto)
        return images

    async def set_review_image_status(self, review_id: int, image_id: int, status: ReviewImageStatus) -> bool:
        i = review_image.c
        found = await self._session.scalar(
            select(i.image_id).where(i.image_id == image_id, i.review_id == review_id).with_for_update()
        )
        if found is None:
            return False
        await self._session.execute(
            sql_update(review_image).where(i.image_id == image_id).values(status=IMAGE_CODES[status])
        )
        await self._session.commit()
        return True

    async def delete_review_image(self, review_id: int, image_id: int) -> str | None:
        i = review_image.c
        path = await self._session.scalar(
            select(i.image).where(i.image_id == image_id, i.review_id == review_id).with_for_update()
        )
        if path is None:
            return None
        await self._session.execute(delete(review_image).where(i.image_id == image_id))
        await self._session.commit()
        return path

    async def update_review(self, review_id: int, request: UpdateReviewRequest, now: datetime) -> bool:
        result = await self._session.execute(
            sql_update(review)
            .where(review.c.review_id == review_id)
            .values(
                author=request.author,
                text=request.text,
                rating=request.rating,
                status=request.published,
                date_modified=now,
            )
        )
        await self._session.commit()
        return result.rowcount > 0

    async def delete_review(self, review_id: int) -> list[str] | None:
        """The old admin left the photos behind; here they go with the review, in one transaction."""
        result = await self._session.execute(delete(review).where(review.c.review_id == review_id))
        if result.rowcount == 0:
            await self._session.rollback()
            return None
        i = review_image.c
        paths = list(await self._session.scalars(select(i.image).where(i.review_id == review_id)))
        await self._session.execute(delete(review_image).where(i.review_id == review_id))
        await self._session.commit()
        return paths

    async def questions(self, state: FeedbackState, page: int, page_size: int) -> PagedResult[QuestionDto]:
        q = question.c
        query = select(q.question_id)
        if state != "all":
            query = query.where(q.status.is_(state == "published"))
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self._session.execute(
            query.with_only_columns(
                q.question_id,
                q.product_id,
                func.coalesce(self._product_name(q.product_id), ""),
                q.name,
                q.telephone,
                q.city,
                q.question,
                q.comment,
                q.status,
                _real(q.date_question),
                _real(q.date_added),
            )
            .order_by(q.question_id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            QuestionDto(
                id=x[0],
                product_id=x[1],
                product_name=x[2],
                name=x[3],
                telephone=x[4],
                city=x[5] or "",
                question=x[6],
                answer=x[7] or "",
                published=bool(x[8]),
                date_question=x[9],
                date_added=x[10],
            )
            for x in rows
        ]
        return PagedResult[QuestionDto](items=items, page=page, page_size=page_size, total_count=total or 0)

    async def update_question(self, question_id: int, request: UpdateQuestionRequest) -> bool:
        result = await self._session.execute(
            sql_update(question)
            .where(question.c.question_id == question_id)
            .values(question=request.question, comment=request.answer, status=request.published)
        )
        await self._session.commit()
        return result.rowcount > 0

    async def delete_question(self, question_id: int) -> bool:
        result = await self._session.execute(delete(question).where(question.c.question_id == question_id))
        await self._session.commit()
        return result.rowcount > 0
