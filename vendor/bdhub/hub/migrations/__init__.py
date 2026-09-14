from .report_analysis_v1 import apply_report_analysis_v1
from .ai_operations_v1 import apply_ai_operations_v1
from .report_analysis_v2 import apply_report_analysis_v2
from .report_analysis_v3 import apply_report_analysis_v3
from .report_analysis_v4 import apply_report_analysis_v4
from .report_analysis_v5 import apply_report_analysis_v5
from .report_analysis_v6 import apply_report_analysis_v6
from .report_analysis_v7 import apply_report_analysis_v7
from .im_operations_v1 import apply_im_operations_v1
from .im_operations_v2 import apply_im_operations_v2
from .im_operations_v3 import apply_im_operations_v3
from .im_operations_v4 import apply_im_operations_v4
from .im_operations_v5 import apply_im_operations_v5
from .im_operations_v6 import apply_im_operations_v6
from .im_operations_v7 import apply_im_operations_v7
from .im_operations_v8 import apply_im_operations_v8
from .im_operations_v9 import apply_im_operations_v9
from .im_operations_v10 import apply_im_operations_v10
from .im_identity_handle_v1 import apply_im_identity_handle_v1
from .im_history_recovery_v1 import apply_im_history_recovery_v1
from .mx_core_v1_schema import apply_mx_core_v1_schema
from .mx_core_v1_data import apply_mx_core_v1_data
from .retire_available_product_pool import apply_retire_available_product_pool
from .mx_only_data_cleanup import apply_mx_only_data_cleanup
from .retire_legacy_tables import apply_retire_legacy_tables
from .creator_classification_multilabel_v1 import (
    apply_creator_classification_multilabel_v1,
)
from .gpm_split_v1 import apply_gpm_split_v1
from .retire_relationship_status_v1 import apply_retire_relationship_status_v1
from .mcn_relation_archive_v1 import apply_mcn_relation_archive_v1
from .mcn_relation_sync_summary_v1 import apply_mcn_relation_sync_summary_v1
from .contact_hint_semantics_v1 import apply_contact_hint_semantics_v1
from .contact_current_integrity_v1 import apply_contact_current_integrity_v1
from .send_task_multi_market_v1 import apply_send_task_multi_market_v1
from .share_link_v1 import apply_share_link_v1
from .share_link_product_v1 import apply_share_link_product_v1
from .share_link_record_v1 import apply_share_link_record_v1
from .public_share_link_v1 import apply_public_share_link_v1
from .public_share_link_country_v1 import apply_public_share_link_country_v1
from .merchant_management_v1 import apply_merchant_management_v1
from .sample_management_v1 import apply_sample_management_v1
from .sample_product_image_v1 import apply_sample_product_image_v1
from .sample_workflow_scope_v1 import apply_sample_workflow_scope_v1
from .sample_workflow_import_v1 import apply_sample_workflow_import_v1
from .sample_review_batch_v1 import apply_sample_review_batch_v1
from .product_catalog_version_v1 import apply_product_catalog_version_v1
from .website_catalog_release_v1 import apply_website_catalog_release_v1
from .product_source_pool_v1 import apply_product_source_pool_v1
from .product_source_detail_v1 import apply_product_source_detail_v1
from .product_pool_qualification_v1 import apply_product_pool_qualification_v1
from .product_category_normalization_v1 import apply_product_category_normalization_v1
from .product_source_append_v1 import apply_product_source_append_v1
from .promotion_site_workflow_v1 import apply_promotion_site_workflow_v1
from .promotion_site_sharelink_batch_v1 import apply_promotion_site_sharelink_batch_v1
from .promotion_site_sample_request_v1 import apply_promotion_site_sample_request_v1
from .promotion_site_sample_request_v2 import apply_promotion_site_sample_request_v2
from .promotion_site_category_v1 import apply_promotion_site_category_v1
from .campaign_ai_category_v1 import apply_campaign_ai_category_v1
from .sample_workspace_simplification_v1 import apply_sample_workspace_simplification_v1
from .sample_lifecycle_status_v1 import apply_sample_lifecycle_status_v1
from .sample_review_queue_v1 import apply_sample_review_queue_v1
from .im_delivery_intent_v1 import apply_im_delivery_intent_v1
from .promotion_site_sharelink_parallel_v1 import (
    apply_promotion_site_sharelink_parallel_v1,
)

__all__ = [
    "apply_report_analysis_v1",
    "apply_report_analysis_v2",
    "apply_report_analysis_v3",
    "apply_report_analysis_v4",
    "apply_report_analysis_v5",
    "apply_report_analysis_v6",
    "apply_report_analysis_v7",
    "apply_im_operations_v1",
    "apply_im_operations_v2",
    "apply_im_operations_v3",
    "apply_im_operations_v4",
    "apply_im_operations_v5",
    "apply_im_operations_v6",
    "apply_im_operations_v7",
    "apply_im_operations_v8",
    "apply_im_operations_v9",
    "apply_im_operations_v10",
    "apply_im_identity_handle_v1",
    "apply_im_history_recovery_v1",
    "apply_mx_core_v1_schema",
    "apply_mx_core_v1_data",
    "apply_retire_available_product_pool",
    "apply_mx_only_data_cleanup",
    "apply_retire_legacy_tables",
    "apply_creator_classification_multilabel_v1",
    "apply_gpm_split_v1",
    "apply_retire_relationship_status_v1",
    "apply_mcn_relation_archive_v1",
    "apply_mcn_relation_sync_summary_v1",
    "apply_contact_hint_semantics_v1",
    "apply_contact_current_integrity_v1",
    "apply_send_task_multi_market_v1",
    "apply_share_link_v1",
    "apply_share_link_product_v1",
    "apply_share_link_record_v1",
    "apply_public_share_link_v1",
    "apply_public_share_link_country_v1",
    "apply_merchant_management_v1",
    "apply_sample_management_v1",
    "apply_sample_product_image_v1",
    "apply_sample_workflow_scope_v1",
    "apply_sample_workflow_import_v1",
    "apply_sample_review_batch_v1",
    "apply_product_catalog_version_v1",
    "apply_website_catalog_release_v1",
    "apply_product_source_pool_v1",
    "apply_product_source_detail_v1",
    "apply_product_pool_qualification_v1",
    "apply_product_category_normalization_v1",
    "apply_product_source_append_v1",
    "apply_promotion_site_workflow_v1",
    "apply_promotion_site_sharelink_batch_v1",
    "apply_promotion_site_sample_request_v1",
    "apply_promotion_site_sample_request_v2",
    "apply_promotion_site_category_v1",
    "apply_campaign_ai_category_v1",
    "apply_sample_workspace_simplification_v1",
    "apply_sample_lifecycle_status_v1",
    "apply_sample_review_queue_v1",
    "apply_im_delivery_intent_v1",
    "apply_promotion_site_sharelink_parallel_v1",
]
