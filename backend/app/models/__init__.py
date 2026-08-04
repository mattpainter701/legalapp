from app.models.tenant import Tenant, TenantSettings
from app.models.platform import PlatformSetting
from app.models.user import User, UserMemory
from app.models.document import Document, Chunk
from app.models.conversation import Conversation, Message, UsageRecord
from app.models.plugin import (
    PracticeProfile,
    TenantPluginEntitlement,
    TenantPluginSetup,
    Matter,
    MatterEvent,
    Renewal,
    Estate,
    EstateEvent,
    MediationCase,
    MediationCaseEvent,
    PromptOverride,
)
from app.models.estate import (
    EstateFiduciary,
    EstateBeneficiary,
    EstateAsset,
    EstateLiability,
    EstateDistribution,
    EstateDeadline,
    EstateAccountingEntry,
)
from app.models.domestic import (
    DomesticCase,
    DomesticParty,
    DomesticChild,
    CustodyArrangement,
    SupportOrder,
    SupportPayment,
    ChildSupportCalculation,
    DomesticDeadline,
    DomesticEvent,
)
from app.models.matter_assignment import MatterAssignment
from app.models.matter_note import MatterNote
from app.models.retainer import Retainer, RetainerTransaction
from app.models.scheduler import SchedulerLog
from app.models.tenant_credential import TenantCredential
from app.models.tenant_oauth_app import TenantOAuthApp
from app.models.user_oauth_token import UserOAuthToken
from app.models.teams_channel_link import TeamsChannelLink
from app.models.teams_notification_setting import TeamsNotificationSetting
from app.models.error_log import ErrorLog
from app.models.integration_sync_run import IntegrationSyncRun
from app.models.contact import Contact, Lead
from app.models.intake_dashboard import (
    IntakeCallDraft,
    LegacyCallRecord,
    PartnerRotationState,
)
from app.models.client_portal import ClientPortalInvite
from app.models.signature import SignatureRequest, SignatureSigner
from app.models.task import Task, TaskEvent
from app.models.scheduled_event import ScheduledEvent
from app.models.communication_log import CommunicationLog
from app.models.document_template import DocumentTemplate
from app.models.document_template_preview import DocumentTemplatePreview
from app.models.cloud_metadata import CloudMetadata
from app.models.mediation import (
    MediationParty,
    MediationInvite,
    MediationAsset,
    MediationDocument,
    MediationProposal,
)
from app.models.smb_agent import SmbAgent
from app.models.smb_share import SmbShare
from app.models.smb_file_index import SmbFileIndex
from app.models.smb_access_log import SmbAccessLog
from app.models.matter_smb_share import MatterSmbShare
from app.models.api_access_log import ApiAccessLog
from app.models.trust_accounting import (
    TrustAccount,
    TrustTransaction,
    TrustBankAccount,
    TrustReconciliation,
)
from app.models.external_import import (
    ExternalSystemConnection,
    ExternalImportRun,
    ExternalRawRow,
    ExternalRecordLink,
)
from app.models.rbac import Role, UserRole
from app.models.mcp_product import MCPProductKey, MCPUsageEvent
from app.models.operator_audit import OperatorAuditLog
from app.models.durable_job import DurableJob
from app.models.office_action_run import OfficeActionRun
from app.models.plugin_skill_run import PluginSkillRun

__all__ = [
    "Tenant",
    "TenantSettings",
    "PlatformSetting",
    "User",
    "UserMemory",
    "Document",
    "Chunk",
    "Conversation",
    "Message",
    "UsageRecord",
    "PracticeProfile",
    "TenantPluginEntitlement",
    "TenantPluginSetup",
    "Matter",
    "MatterEvent",
    "Renewal",
    "Estate",
    "EstateEvent",
    "EstateFiduciary",
    "EstateBeneficiary",
    "EstateAsset",
    "EstateLiability",
    "EstateDistribution",
    "EstateDeadline",
    "EstateAccountingEntry",
    "MediationCase",
    "MediationCaseEvent",
    "MediationParty",
    "MediationInvite",
    "MediationAsset",
    "MediationDocument",
    "MediationProposal",
    "DomesticCase",
    "DomesticParty",
    "DomesticChild",
    "CustodyArrangement",
    "SupportOrder",
    "SupportPayment",
    "ChildSupportCalculation",
    "DomesticDeadline",
    "DomesticEvent",
    "MatterAssignment",
    "MatterNote",
    "Retainer",
    "RetainerTransaction",
    "SchedulerLog",
    "TenantCredential",
    "TenantOAuthApp",
    "UserOAuthToken",
    "TeamsChannelLink",
    "TeamsNotificationSetting",
    "ErrorLog",
    "IntegrationSyncRun",
    "Contact",
    "Lead",
    "LegacyCallRecord",
    "IntakeCallDraft",
    "PartnerRotationState",
    "ClientPortalInvite",
    "SignatureRequest",
    "SignatureSigner",
    "Task",
    "TaskEvent",
    "ScheduledEvent",
    "CommunicationLog",
    "DocumentTemplate",
    "DocumentTemplatePreview",
    "PromptOverride",
    "CloudMetadata",
    "SmbAgent",
    "SmbShare",
    "SmbFileIndex",
    "SmbAccessLog",
    "MatterSmbShare",
    "ApiAccessLog",
    "TrustAccount",
    "TrustTransaction",
    "TrustBankAccount",
    "TrustReconciliation",
    "ExternalSystemConnection",
    "ExternalImportRun",
    "ExternalRawRow",
    "ExternalRecordLink",
    "Role",
    "UserRole",
    "MCPProductKey",
    "MCPUsageEvent",
    "OperatorAuditLog",
    "DurableJob",
    "OfficeActionRun",
    "PluginSkillRun",
]
