"""Reviews and customer questions from the storefront: moderation of the old admin (Python first, item 51).

The storefront shows both as plain text (escaped), so they are kept as text, not HTML. A question's answer is the
old site's comment column; a question with an answer and status on is shown on the product page.

Customers may add photos to a review (dd_review_image, item 83). Each photo is moderated on its own; the storefront
shows only published ones. Deleting a photo or a review also deletes the files the storefront saved in
review_photos/."""

import logging
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Literal, Protocol

from pydantic import Field

from newerp.application.common import Clock, Dto, NotFoundError, PagedResult, normalize_paging
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")
logger = logging.getLogger(__name__)

FeedbackState = Literal["all", "pending", "published"]
ReviewImageStatus = Literal["pending", "published", "rejected"]

# Where the storefront saves review photos (catalog/controller/product/product.php). Only files there are deleted:
# a row pointing elsewhere may share a picture with a product.
REVIEW_PHOTO_FOLDER = "review_photos/"


def is_review_photo(path: str) -> bool:
    """A file of review_photos/ itself: no "..", no backslash, so "review_photos/../catalog/x.jpg" is not one."""
    parts = PurePosixPath(path).parts
    return path.startswith(REVIEW_PHOTO_FOLDER) and "\\" not in path and ".." not in parts and len(parts) >= 2


class ReviewImageDto(Dto):
    """path: from the storefront's image/ folder."""

    id: int
    path: str
    status: ReviewImageStatus


class ReviewDto(Dto):
    id: int
    product_id: int
    product_name: str
    author: str
    text: str
    rating: int
    published: bool
    date_added: datetime
    images: list[ReviewImageDto] = []


class SetReviewImageStatusRequest(Dto):
    status: ReviewImageStatus


class UpdateReviewRequest(Dto):
    author: str = Field(default="", max_length=64)
    text: str = Field(default="", max_length=10_000)
    rating: int = Field(default=5, ge=1, le=5)
    published: bool = False


class QuestionDto(Dto):
    id: int
    product_id: int
    product_name: str
    name: str
    telephone: str
    city: str
    question: str
    answer: str
    published: bool
    date_question: date | None
    date_added: datetime | None


class UpdateQuestionRequest(Dto):
    question: str = Field(default="", max_length=10_000)
    answer: str = Field(default="", max_length=10_000)
    published: bool = False


class FeedbackStore(Protocol):
    async def reviews(self, state: FeedbackState, page: int, page_size: int) -> PagedResult[ReviewDto]: ...

    async def update_review(self, review_id: int, request: UpdateReviewRequest, now: datetime) -> bool: ...

    async def delete_review(self, review_id: int) -> list[str] | None:
        """The paths of the review's photos, which are deleted with it; None when there is no such review."""

    async def set_review_image_status(self, review_id: int, image_id: int, status: ReviewImageStatus) -> bool: ...

    async def delete_review_image(self, review_id: int, image_id: int) -> str | None:
        """The deleted photo's path; None when the review has no such photo."""

    async def questions(self, state: FeedbackState, page: int, page_size: int) -> PagedResult[QuestionDto]: ...

    async def update_question(self, question_id: int, request: UpdateQuestionRequest) -> bool: ...

    async def delete_question(self, question_id: int) -> bool: ...


class PhotoFolder(Protocol):
    """The storefront's image folder; enabled only when the ERP can write it (NEWERP_PICTURES__IMAGE_DIR)."""

    @property
    def enabled(self) -> bool: ...

    async def delete(self, path: str) -> None: ...


class FeedbackService:
    def __init__(self, store: FeedbackStore, clock: Clock, photos: PhotoFolder | None = None) -> None:
        self._store = store
        self._clock = clock
        self._photos = photos

    async def reviews(self, state: FeedbackState = "all", page: int = 1, page_size: int = 20) -> PagedResult[ReviewDto]:
        page, page_size = normalize_paging(page, page_size)
        return await self._store.reviews(state, page, page_size)

    async def update_review(self, review_id: int, request: UpdateReviewRequest, user_id: int) -> None:
        if not request.author.strip() or not request.text.strip():
            raise DomainError("Відгук має мати автора й текст.")
        clean = request.model_copy(update={"author": request.author.strip(), "text": request.text.strip()})
        if not await self._store.update_review(review_id, clean, self._clock.now().replace(microsecond=0)):
            raise NotFoundError(f"Відгуку {review_id} немає.")
        audit.info("review moderated: %s, published %s, by user %s", review_id, request.published, user_id)

    async def delete_review(self, review_id: int, user_id: int) -> None:
        paths = await self._store.delete_review(review_id)
        if paths is None:
            raise NotFoundError(f"Відгуку {review_id} немає.")
        for path in paths:
            await self._delete_file(path)
        audit.info("review deleted: %s with %s photos, by user %s", review_id, len(paths), user_id)

    async def set_review_image_status(
        self, review_id: int, image_id: int, request: SetReviewImageStatusRequest, user_id: int
    ) -> None:
        if not await self._store.set_review_image_status(review_id, image_id, request.status):
            raise NotFoundError(f"Фото {image_id} у відгуку {review_id} немає.")
        audit.info(
            "review photo moderated: %s of review %s, %s, by user %s", image_id, review_id, request.status, user_id
        )

    async def delete_review_image(self, review_id: int, image_id: int, user_id: int) -> None:
        path = await self._store.delete_review_image(review_id, image_id)
        if path is None:
            raise NotFoundError(f"Фото {image_id} у відгуку {review_id} немає.")
        await self._delete_file(path)
        audit.info("review photo deleted: %s of review %s, by user %s", image_id, review_id, user_id)

    async def _delete_file(self, path: str) -> None:
        """Like the old admin: the row goes first, a file that can't be removed only leaves a warning."""
        if self._photos is None or not self._photos.enabled or not is_review_photo(path):
            return
        try:
            await self._photos.delete(path)
        except (OSError, ValueError) as error:
            logger.warning("Review photo file %s was not deleted: %s", path, type(error).__name__)

    async def questions(
        self, state: FeedbackState = "all", page: int = 1, page_size: int = 20
    ) -> PagedResult[QuestionDto]:
        page, page_size = normalize_paging(page, page_size)
        return await self._store.questions(state, page, page_size)

    async def update_question(self, question_id: int, request: UpdateQuestionRequest, user_id: int) -> None:
        if not request.question.strip():
            raise DomainError("Питання не може бути порожнім.")
        if request.published and not request.answer.strip():
            raise DomainError("Опублікувати можна лише питання з відповіддю.")
        clean = request.model_copy(update={"question": request.question.strip(), "answer": request.answer.strip()})
        if not await self._store.update_question(question_id, clean):
            raise NotFoundError(f"Питання {question_id} немає.")
        audit.info("question answered: %s, published %s, by user %s", question_id, request.published, user_id)

    async def delete_question(self, question_id: int, user_id: int) -> None:
        if not await self._store.delete_question(question_id):
            raise NotFoundError(f"Питання {question_id} немає.")
        audit.info("question deleted: %s, by user %s", question_id, user_id)
