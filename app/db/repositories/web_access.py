from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.schemas import BusinessAccess
from app.db.models import BusinessModel, UserModel, WebUserAccessModel


class SqlWebAccessRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def list_access(self, subject: str) -> list[BusinessAccess]:
        with self.session_factory() as session:
            rows = session.execute(
                select(WebUserAccessModel, UserModel, BusinessModel)
                .join(
                    UserModel,
                    (
                        (UserModel.id == WebUserAccessModel.user_id)
                        & (UserModel.business_id == WebUserAccessModel.business_id)
                    ),
                )
                .join(BusinessModel, BusinessModel.id == WebUserAccessModel.business_id)
                .where(WebUserAccessModel.auth_subject == subject, WebUserAccessModel.is_active)
                .order_by(WebUserAccessModel.business_id)
            ).all()
            return [
                BusinessAccess(
                    business_id=link.business_id,
                    business_name=business.name,
                    user_id=user.id,
                    display_name=user.display_name,
                    role=user.role,
                )
                for link, user, business in rows
            ]
