"""Link an existing Supabase Auth user to a business; dry-run unless --apply.

Run as an administrator with DATABASE_URL configured. Never accepts passwords,
creates Auth users or sends email. Existing business users can be reused explicitly.
"""

import argparse
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text

from app.db.models import BusinessModel, UserModel, WebUserAccessModel
from app.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-subject", required=True, type=UUID)
    parser.add_argument("--email", required=True)
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--user-id", help="Reuse an existing local user in the same business")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if SessionLocal is None:
        parser.error("DATABASE_URL is required")
    subject = str(args.auth_subject)
    with SessionLocal() as session:
        auth_user = (
            session.execute(
                text(
                    "SELECT email, banned_until FROM auth.users WHERE id = CAST(:subject AS uuid)"
                ),
                {"subject": subject},
            )
            .mappings()
            .one_or_none()
        )
        if auth_user is None or (auth_user["email"] or "").casefold() != args.email.casefold():
            parser.error("Auth subject does not match the expected email")
        if auth_user["banned_until"] is not None:
            parser.error("Review the Auth user ban before granting access")
        business = session.get(BusinessModel, args.business_id)
        if business is None:
            parser.error("Business does not exist")
        link = session.get(WebUserAccessModel, (subject, args.business_id))
        user_id = args.user_id or (
            link.user_id
            if link
            else str(uuid5(NAMESPACE_URL, f"stock-ai:web:{subject}:{args.business_id}"))
        )
        user = session.scalar(select(UserModel).where(UserModel.id == user_id))
        if user and user.business_id != args.business_id:
            parser.error("Local user belongs to another business")
        if args.user_id and user is None:
            parser.error("Explicit local user does not exist")
        if link and link.user_id != user_id:
            parser.error("An existing link cannot be reassigned by this command")
        if not args.apply:
            print(f"Dry run: grant {subject} access to {args.business_id} as {user_id}")
            return
        if user is None:
            user = UserModel(
                id=user_id,
                business_id=args.business_id,
                display_name=args.display_name,
                email=args.email,
                role="operator",
            )
            session.add(user)
            session.flush()
        if link is None:
            session.add(
                WebUserAccessModel(
                    auth_subject=subject,
                    business_id=args.business_id,
                    user_id=user.id,
                    is_active=True,
                )
            )
        else:
            link.is_active = True
        session.commit()
        print(f"Access granted to business {args.business_id} for local user {user.id}")


if __name__ == "__main__":
    main()
