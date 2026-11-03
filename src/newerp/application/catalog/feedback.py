"""Reviews and customer questions from the storefront: moderation of the old admin (Python first, item 51).

The storefront shows both as plain text (escaped), so they are kept as text, not HTML. A question's answer is the
old site's comment column; a question with an answer and status on is shown on the product page."""

import logging
from datetime import date, datetime
from typing import Literal, Protocol

from pydantic import Field

from newerp.application.common import Clock, Dto, NotFoundError, PagedResult, normalize_paging
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")

FeedbackState = Literal["all", "pending", "published"]


class ReviewDto(Dto):
    id: int
    product_id: int
    product_name: str
    author: str
    text: str
    rating: int
    published: bool
    date_added: datetime


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

    async def delete_review(self, review_id: int) -> bool: ...

    async def questions(self, state: FeedbackState, page: int, page_size: int) -> PagedResult[QuestionDto]: ...

    async def update_question(self, question_id: int, request: UpdateQuestionRequest) -> bool: ...

    async def delete_question(self, question_id: int) -> bool: ...


class FeedbackService:
    def __init__(self, store: FeedbackStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

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
        if not await self._store.delete_review(review_id):
            raise NotFoundError(f"Відгуку {review_id} немає.")
        audit.info("review deleted: %s, by user %s", review_id, user_id)

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
