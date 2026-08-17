"""HTTP for the signing flow. Nothing else.

Every rule this endpoint used to enforce inline now lives in
app/services/agreements.py: what makes a link spent, how many attempts a code
gets, when the stage moves. What is left here is the web layer's actual job —
read the request, call one use case, shape the response.

`issue` is staff-only. The token endpoints are public because the professional
signing has no staff login; the token itself is the credential.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from fastapi.responses import Response

from app.adapters.pdf import agreement as agreement_pdf
from app.api.clients import client_ip
from app.api.deps import AgreementServiceDep, AssetServiceDep, SettingsDep
from app.domain import terms as terms_module
from app.models import Staff
from app.schemas import (
    AgreementIssueOut,
    AssetOut,
    AgreementPublicOut,
    AgreementPublicProfessional,
    AgreementSignOut,
    AgreementSignRequest,
    AgreementVerifyOut,
    AgreementVerifyRequest,
)
from app.security import current_staff
from app.services.assets import Upload

router = APIRouter(prefix="/api/agreements", tags=["agreements"])


@router.post("/issue/{professional_id}", response_model=AgreementIssueOut)
def issue_agreement(
    professional_id: int,
    service: AgreementServiceDep,
    staff: Staff = Depends(current_staff),
) -> AgreementIssueOut:
    issued = service.issue(professional_id, staff=staff)
    out = AgreementIssueOut.model_validate(issued.agreement)
    # Delivery is reported to the caller rather than only logged: they are on
    # the phone and need to know whether to read the link out.
    out.link = issued.link
    out.email_sent = issued.email_sent
    out.email_detail = issued.email_detail
    return out


@router.get("/{token}", response_model=AgreementPublicOut)
def get_agreement(token: str, service: AgreementServiceDep) -> AgreementPublicOut:
    agreement, professional = service.for_signing_page(token)
    return AgreementPublicOut(
        terms_version=agreement.terms_version,
        # So the page knows which step is outstanding after a reload.
        signed_at=agreement.signed_at,
        email_verified_at=agreement.email_verified_at,
        signed_email=agreement.signed_email,
        # The frozen clauses, so the page renders exactly what will be held
        # against the signature rather than whatever the constant says today.
        clauses=terms_module.clauses(agreement.terms_version),
        consent_statement=terms_module.CONSENT_STATEMENT,
        sent_at=agreement.sent_at,
        expires_at=agreement.expires_at,
        professional=AgreementPublicProfessional(
            id=professional.id,
            business_name=professional.business_name,
            contact_name=professional.contact_name,
            phone=professional.phone,
            email=professional.email,
            city=professional.city,
            region=professional.region,
            profession=professional.profession.label if professional.profession else None,
            photo=professional.photo,
        ),
    )


@router.post("/{token}/sign", response_model=AgreementSignOut)
def sign_agreement(
    token: str,
    payload: AgreementSignRequest,
    request: Request,
    service: AgreementServiceDep,
    settings: SettingsDep,
) -> AgreementSignOut:
    # The IP is read here because only the web layer has a request. It is
    # handed to the service as a fact, not fetched by it.
    result = service.sign(
        token,
        signed_name=payload.signed_name,
        signature_image=payload.signature_image,
        licence=payload.licence,
        vat_number=payload.vat_number,
        email=payload.email,
        phone=payload.phone,
        profession=payload.profession,
        photo=payload.photo,
        agreed=payload.agreed,
        ip=client_ip(request, settings),
        user_agent=request.headers.get("user-agent"),
    )
    return AgreementSignOut(
        professional_id=result.professional.id,
        signed_at=result.agreement.signed_at,
        stage=result.professional.stage,
        verification_required=True,
        code_sent=result.code_sent,
        code_sent_to=result.agreement.signed_email,
    )


@router.post("/{token}/photo", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
def upload_signing_photo(
    token: str,
    service: AgreementServiceDep,
    assets: AssetServiceDep,
    file: UploadFile = File(...),
) -> AssetOut:
    """The photo a professional attaches while signing.

    It used to travel inside the sign payload as a base64 data: URL and land in
    the professionals row. That put a few hundred kilobytes into every response
    that selects the column — the worklist returns fifty at a time. It goes to
    object storage now, like every other photo.
    """
    professional = service.open_for_upload(token)
    return AssetOut.model_validate(
        assets.upload_photo(
            professional.id,
            Upload(filename=file.filename,
                   content_type=file.content_type or "application/octet-stream",
                   data=file.file.read()),
            actor_label="professional (signing)",
        )
    )


@router.post("/{token}/verify", response_model=AgreementVerifyOut)
def verify_agreement(
    token: str, payload: AgreementVerifyRequest, service: AgreementServiceDep,
) -> AgreementVerifyOut:
    professional, agreement = service.verify(token, payload.code)
    return AgreementVerifyOut(
        professional_id=professional.id,
        verified_at=agreement.email_verified_at,
        stage=professional.stage,
    )


@router.post("/{token}/resend", response_model=AgreementVerifyOut)
def resend_code(token: str, service: AgreementServiceDep) -> AgreementVerifyOut:
    professional, sent = service.resend_code(token)
    return AgreementVerifyOut(
        professional_id=professional.id,
        verified_at=None,
        stage=professional.stage,
        code_sent=sent,
    )


def _pdf(agreement, professional) -> Response:
    name = agreement.signed_name or professional.business_name or "agreement"
    safe = "".join(c if c.isalnum() or c in "-_ " else "" for c in name).strip() or "agreement"
    return Response(
        content=agreement_pdf.render(agreement, professional),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="Vilaow agreement - {safe}.pdf"',
            # A signed agreement is personal data; it must not sit in a proxy.
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/{token}/pdf")
def agreement_pdf_by_token(token: str, service: AgreementServiceDep) -> Response:
    """The signer's own copy. The token is the credential, as it is for signing."""
    return _pdf(*service.for_pdf(service.by_token(token)))


@router.get("/professional/{professional_id}/pdf")
def agreement_pdf_for_staff(
    professional_id: int,
    service: AgreementServiceDep,
    _staff: Staff = Depends(current_staff),
) -> Response:
    """Vilaow's copy — the reason the row stores the terms verbatim."""
    return _pdf(*service.for_pdf(service.latest_signed_for(professional_id)))


__all__ = ["router", "status"]
