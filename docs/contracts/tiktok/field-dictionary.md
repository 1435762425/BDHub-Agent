# 本轮实际返回字段字典

只保存字段路径、JSON类型和该类型出现次数，不保存原始业务值。数组内路径合并，次数不是独立达人数；空对象/空数组不证明字段不支持。总计490个按请求计的路径观察，不是490个唯一业务字段。

## MX / campaigns

GET `/api/v1/affiliate/partner/campaign/list`；2026-09-13T03:20:08.842287+00:00；单页样本。

| 字段路径 | 类型及出现次数 |
|---|---|
| `$` | object:1 |
| `$.code` | number:1 |
| `$.data` | object:1 |
| `$.data.campaign` | array:1 |
| `$.data.campaign[]` | object:10 |
| `$.data.campaign[].campaign_id` | string:10 |
| `$.data.campaign[].campaign_statistics` | object:10 |
| `$.data.campaign[].campaign_statistics.applied_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.closed_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.joined_seller_num` | number:10 |
| `$.data.campaign[].campaign_statistics.ongoing_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.pending_closed_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.pending_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.rejected_product_num` | number:10 |
| `$.data.campaign[].campaign_type` | number:10 |
| `$.data.campaign[].campaign_version` | number:10 |
| `$.data.campaign[].commission` | string:10 |
| `$.data.campaign[].crs_campaign_type` | number:10 |
| `$.data.campaign[].description` | string:10 |
| `$.data.campaign[].is_locked` | boolean:10 |
| `$.data.campaign[].main_campaign_id` | string:10 |
| `$.data.campaign[].name` | string:10 |
| `$.data.campaign[].new_products_count` | number:10 |
| `$.data.campaign[].partner_join_status` | number:10 |
| `$.data.campaign[].promotion_end_time` | string:10 |
| `$.data.campaign[].promotion_start_time` | string:10 |
| `$.data.campaign[].region` | string:10 |
| `$.data.campaign[].registration_end_time` | string:10 |
| `$.data.campaign[].registration_start_time` | string:10 |
| `$.data.campaign[].sample_budget` | object:10 |
| `$.data.campaign[].seller_campaign_extra` | object:10 |
| `$.data.campaign[].seller_campaign_extra.max_commission_rate` | string:9 |
| `$.data.campaign[].seller_campaign_extra.max_open_plan_commission_delta` | string:7 |
| `$.data.campaign[].seller_campaign_extra.min_commission_rate` | string:10 |
| `$.data.campaign[].seller_campaign_extra.min_open_plan_commission_delta` | string:6 |
| `$.data.campaign[].seller_campaign_extra.sample_availability` | string:10 |
| `$.data.campaign[].seller_info` | object:10 |
| `$.data.campaign[].seller_info.shop_code` | string:9 |
| `$.data.campaign[].seller_info.shop_icon` | object:9 |
| `$.data.campaign[].seller_info.shop_icon.uri` | string:9 |
| `$.data.campaign[].seller_info.shop_icon.url_list` | array:9 |
| `$.data.campaign[].seller_info.shop_icon.url_list[]` | string:18 |
| `$.data.campaign[].seller_info.shop_name` | string:9 |
| `$.data.campaign[].shop_code` | array:1 |
| `$.data.campaign[].shop_code[]` | string:1 |
| `$.data.campaign[].status` | number:10 |
| `$.data.campaign_join_status_counts` | object:1 |
| `$.data.campaign_join_status_counts.1` | number:1 |
| `$.data.campaign_join_status_counts.2` | number:1 |
| `$.data.campaign_join_status_counts.3` | number:1 |
| `$.data.campaign_ongoing_quota_map` | object:1 |
| `$.data.campaign_ongoing_quota_map.1` | object:1 |
| `$.data.campaign_ongoing_quota_map.1.max_count` | number:1 |
| `$.data.campaign_ongoing_quota_map.1.ongoing_count` | number:1 |
| `$.data.status_count` | null:1 |
| `$.data.termination_tooltip_info` | object:1 |
| `$.data.termination_tooltip_info.campaign_with_pending_closed_product_count` | string:1 |
| `$.data.total_num` | number:1 |
| `$.msg` | string:1 |

## MX / selected

POST `/api/v1/affiliate/partner/product/pick_up/list`；2026-09-13T03:20:11.847184+00:00；单页样本。

| 字段路径 | 类型及出现次数 |
|---|---|
| `$` | object:1 |
| `$.code` | number:1 |
| `$.data` | array:1 |
| `$.data[]` | object:5 |
| `$.data[].campaign_info` | object:5 |
| `$.data[].campaign_info.campaign_id` | string:5 |
| `$.data[].campaign_info.contact_info` | object:1 |
| `$.data[].campaign_info.crs_campaign_type` | number:5 |
| `$.data[].campaign_info.name` | string:5 |
| `$.data[].campaign_info.promotion_end_time` | string:5 |
| `$.data[].campaign_info.promotion_start_time` | string:5 |
| `$.data[].campaign_product` | object:5 |
| `$.data[].campaign_product.open_collab_ads_commission_percent` | string:4 |
| `$.data[].campaign_product.plan_commission_percent` | string:5 |
| `$.data[].campaign_product.plan_type` | number:5 |
| `$.data[].campaign_product.product_id` | string:5 |
| `$.data[].campaign_product.product_name` | string:5 |
| `$.data[].campaign_product.product_price` | object:5 |
| `$.data[].campaign_product.product_price.currency` | object:5 |
| `$.data[].campaign_product.product_price.currency.symbol` | string:5 |
| `$.data[].campaign_product.product_price.max_price` | string:5 |
| `$.data[].campaign_product.product_price.min_price` | string:5 |
| `$.data[].campaign_product.product_rating` | number:5 |
| `$.data[].campaign_product.product_review_count` | string:5 |
| `$.data[].campaign_product.product_sales` | string:5 |
| `$.data[].campaign_product.product_status` | number:5 |
| `$.data[].campaign_product.product_thumbnail` | object:5 |
| `$.data[].campaign_product.product_thumbnail.uri` | string:5 |
| `$.data[].campaign_product.product_thumbnail.url_list` | array:5 |
| `$.data[].campaign_product.product_thumbnail.url_list[]` | string:10 |
| `$.data[].campaign_product.product_type` | string:1 |
| `$.data[].campaign_product.shop_experience_score` | number:5 |
| `$.data[].campaign_product.shop_name` | string:5 |
| `$.data[].campaign_product.shop_score` | string:5 |
| `$.data[].campaign_product.stock` | string:5 |
| `$.data[].campaign_product.total_ads_commission_percent` | string:1 |
| `$.data[].campaign_product.total_commission_percent` | string:5 |
| `$.data[].has_free_sample` | boolean:5 |
| `$.data[].product_source` | number:5 |
| `$.msg` | string:1 |
| `$.total_num` | number:1 |

## MX / samples

POST `/api/v1/affiliate/partner/sample/records/list`；2026-09-13T03:20:13.976400+00:00；单页样本。

| 字段路径 | 类型及出现次数 |
|---|---|
| `$` | object:1 |
| `$.code` | number:1 |
| `$.data` | object:1 |
| `$.data.sample_records` | array:1 |
| `$.data.sample_records[]` | object:5 |
| `$.data.sample_records[].apply_id` | string:5 |
| `$.data.sample_records[].campaign_info` | object:5 |
| `$.data.sample_records[].campaign_info.campaign_id` | string:5 |
| `$.data.sample_records[].campaign_info.campaign_statistics` | object:5 |
| `$.data.sample_records[].campaign_info.campaign_type` | number:5 |
| `$.data.sample_records[].campaign_info.campaign_version` | number:5 |
| `$.data.sample_records[].campaign_info.commission` | string:5 |
| `$.data.sample_records[].campaign_info.contact_info` | object:5 |
| `$.data.sample_records[].campaign_info.contact_info.campaign_contact_info` | array:5 |
| `$.data.sample_records[].campaign_info.contact_info.campaign_contact_info[]` | object:30 |
| `$.data.sample_records[].campaign_info.contact_info.campaign_contact_info[].region_code` | string:25 |
| `$.data.sample_records[].campaign_info.contact_info.campaign_contact_info[].type` | number:30 |
| `$.data.sample_records[].campaign_info.contact_info.campaign_contact_info[].value` | string:30 |
| `$.data.sample_records[].campaign_info.contact_info.email` | string:5 |
| `$.data.sample_records[].campaign_info.contact_info.phone` | string:5 |
| `$.data.sample_records[].campaign_info.contact_info.phone_region_code` | string:5 |
| `$.data.sample_records[].campaign_info.contact_info.whatsapp` | string:5 |
| `$.data.sample_records[].campaign_info.contact_info.whatsapp_region_code` | string:5 |
| `$.data.sample_records[].campaign_info.crs_campaign_type` | number:5 |
| `$.data.sample_records[].campaign_info.description` | string:5 |
| `$.data.sample_records[].campaign_info.is_attached_business_profile` | boolean:4 |
| `$.data.sample_records[].campaign_info.is_locked` | boolean:5 |
| `$.data.sample_records[].campaign_info.main_campaign_id` | string:5 |
| `$.data.sample_records[].campaign_info.name` | string:5 |
| `$.data.sample_records[].campaign_info.partner_join_status` | number:5 |
| `$.data.sample_records[].campaign_info.promotion_end_time` | string:5 |
| `$.data.sample_records[].campaign_info.promotion_start_time` | string:5 |
| `$.data.sample_records[].campaign_info.region` | string:5 |
| `$.data.sample_records[].campaign_info.registration_end_time` | string:5 |
| `$.data.sample_records[].campaign_info.registration_start_time` | string:5 |
| `$.data.sample_records[].campaign_info.sample_budget` | object:5 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra` | object:5 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference` | object:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.age_range` | array:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.age_range[]` | number:3 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.category_setting` | array:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.category_setting[]` | object:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.category_setting[].category_id` | string:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.content_format` | array:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.content_format[]` | number:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.content_style` | array:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.content_style[]` | string:7 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.follower_range` | array:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.follower_range[]` | number:4 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.creator_preference.notes` | string:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.exclusive` | array:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.exclusive[]` | string:2 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.max_commission_rate` | string:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.min_commission_rate` | string:5 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.objective` | array:1 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.objective[]` | string:3 |
| `$.data.sample_records[].campaign_info.seller_campaign_extra.sample_availability` | string:5 |
| `$.data.sample_records[].campaign_info.seller_info` | object:5 |
| `$.data.sample_records[].campaign_info.seller_type` | number:1 |
| `$.data.sample_records[].campaign_info.shop_code` | array:3 |
| `$.data.sample_records[].campaign_info.shop_code[]` | string:3 |
| `$.data.sample_records[].campaign_info.status` | number:5 |
| `$.data.sample_records[].creator_info` | object:5 |
| `$.data.sample_records[].creator_info.cl_pay_gmv_30d` | string:5 |
| `$.data.sample_records[].creator_info.follower_num` | string:5 |
| `$.data.sample_records[].creator_info.id` | string:5 |
| `$.data.sample_records[].creator_info.image_url` | string:5 |
| `$.data.sample_records[].creator_info.is_fast_growing` | boolean:5 |
| `$.data.sample_records[].creator_info.nick_name` | string:5 |
| `$.data.sample_records[].creator_info.oec_id` | string:5 |
| `$.data.sample_records[].creator_info.post_rate` | string:5 |
| `$.data.sample_records[].creator_info.user_name` | string:5 |
| `$.data.sample_records[].creator_info.video_avg_view_cnt` | string:5 |
| `$.data.sample_records[].creator_info.video_publish_cnt_30d` | string:5 |
| `$.data.sample_records[].is_posted` | boolean:5 |
| `$.data.sample_records[].is_product_in_showcase` | boolean:5 |
| `$.data.sample_records[].main_order_id` | string:5 |
| `$.data.sample_records[].product_info` | object:5 |
| `$.data.sample_records[].product_info.creator_commission_percent` | string:5 |
| `$.data.sample_records[].product_info.is_register_again` | boolean:5 |
| `$.data.sample_records[].product_info.marked` | boolean:5 |
| `$.data.sample_records[].product_info.open_collab_ads_commission_percent` | string:2 |
| `$.data.sample_records[].product_info.partner_commission_percent` | string:5 |
| `$.data.sample_records[].product_info.plan_commission_percent` | string:5 |
| `$.data.sample_records[].product_info.plan_type` | number:5 |
| `$.data.sample_records[].product_info.product_id` | string:5 |
| `$.data.sample_records[].product_info.product_label` | object:5 |
| `$.data.sample_records[].product_info.product_label.deboost_code` | number:5 |
| `$.data.sample_records[].product_info.product_name` | string:5 |
| `$.data.sample_records[].product_info.product_price` | object:5 |
| `$.data.sample_records[].product_info.product_price.max_price` | string:5 |
| `$.data.sample_records[].product_info.product_price.min_price` | string:5 |
| `$.data.sample_records[].product_info.product_sales` | string:5 |
| `$.data.sample_records[].product_info.product_status` | number:5 |
| `$.data.sample_records[].product_info.product_thumbnail` | object:5 |
| `$.data.sample_records[].product_info.product_thumbnail.uri` | string:5 |
| `$.data.sample_records[].product_info.product_thumbnail.url_list` | array:5 |
| `$.data.sample_records[].product_info.product_thumbnail.url_list[]` | string:10 |
| `$.data.sample_records[].product_info.sample_quota` | number:5 |
| `$.data.sample_records[].product_info.seller_contact_info` | object:5 |
| `$.data.sample_records[].product_info.seller_contact_info.campaign_contact_info` | array:5 |
| `$.data.sample_records[].product_info.seller_contact_info.campaign_contact_info[]` | object:30 |
| `$.data.sample_records[].product_info.seller_contact_info.campaign_contact_info[].region_code` | string:25 |
| `$.data.sample_records[].product_info.seller_contact_info.campaign_contact_info[].type` | number:30 |
| `$.data.sample_records[].product_info.seller_contact_info.campaign_contact_info[].value` | string:30 |
| `$.data.sample_records[].product_info.seller_contact_info.email` | string:5 |
| `$.data.sample_records[].product_info.seller_contact_info.phone` | string:5 |
| `$.data.sample_records[].product_info.seller_contact_info.phone_region_code` | string:5 |
| `$.data.sample_records[].product_info.seller_contact_info.whatsapp` | string:5 |
| `$.data.sample_records[].product_info.seller_contact_info.whatsapp_region_code` | string:5 |
| `$.data.sample_records[].product_info.seller_id` | string:5 |
| `$.data.sample_records[].product_info.shop_code` | string:5 |
| `$.data.sample_records[].product_info.shop_experience_score` | number:5 |
| `$.data.sample_records[].product_info.shop_icon` | object:5 |
| `$.data.sample_records[].product_info.shop_icon.uri` | string:5 |
| `$.data.sample_records[].product_info.shop_icon.url_list` | array:5 |
| `$.data.sample_records[].product_info.shop_icon.url_list[]` | string:10 |
| `$.data.sample_records[].product_info.shop_name` | string:5 |
| `$.data.sample_records[].product_info.shop_score` | string:5 |
| `$.data.sample_records[].product_info.sku_info` | string:5 |
| `$.data.sample_records[].product_info.stock` | string:5 |
| `$.data.sample_records[].product_info.total_ads_commission_percent` | string:1 |
| `$.data.sample_records[].product_info.total_commission_percent` | string:5 |
| `$.data.sample_records[].room_count` | number:5 |
| `$.data.sample_records[].sample_main_id` | string:5 |
| `$.data.sample_records[].sample_status` | number:5 |
| `$.data.sample_records[].status` | number:5 |
| `$.data.sample_records[].video_count` | number:5 |
| `$.data.sample_status_num_map` | object:1 |
| `$.data.sample_status_num_map.CANCELED` | number:1 |
| `$.data.sample_status_num_map.COMPLETED` | number:1 |
| `$.data.sample_status_num_map.CONTENT_PENDING` | number:1 |
| `$.data.sample_status_num_map.READY_TO_SHIP_1` | number:1 |
| `$.data.sample_status_num_map.SHIPPED` | number:1 |
| `$.data.sample_status_num_map.TO_REVIEW_1` | number:1 |
| `$.data.total` | number:1 |
| `$.msg` | string:1 |

## IT / campaigns

GET `/api/v1/affiliate/partner/campaign/list`；2026-09-13T03:20:17.725006+00:00；单页样本。

| 字段路径 | 类型及出现次数 |
|---|---|
| `$` | object:1 |
| `$.code` | number:1 |
| `$.data` | object:1 |
| `$.data.campaign` | array:1 |
| `$.data.campaign[]` | object:10 |
| `$.data.campaign[].campaign_id` | string:10 |
| `$.data.campaign[].campaign_statistics` | object:10 |
| `$.data.campaign[].campaign_statistics.applied_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.closed_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.joined_seller_num` | number:10 |
| `$.data.campaign[].campaign_statistics.ongoing_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.pending_closed_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.pending_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.rejected_product_num` | number:10 |
| `$.data.campaign[].campaign_type` | number:10 |
| `$.data.campaign[].campaign_version` | number:10 |
| `$.data.campaign[].commission` | string:10 |
| `$.data.campaign[].crs_campaign_type` | number:10 |
| `$.data.campaign[].description` | string:10 |
| `$.data.campaign[].is_locked` | boolean:10 |
| `$.data.campaign[].main_campaign_id` | string:10 |
| `$.data.campaign[].name` | string:10 |
| `$.data.campaign[].new_products_count` | number:10 |
| `$.data.campaign[].partner_join_status` | number:10 |
| `$.data.campaign[].promotion_end_time` | string:10 |
| `$.data.campaign[].promotion_start_time` | string:10 |
| `$.data.campaign[].region` | string:10 |
| `$.data.campaign[].registration_end_time` | string:10 |
| `$.data.campaign[].registration_start_time` | string:10 |
| `$.data.campaign[].sample_budget` | object:10 |
| `$.data.campaign[].seller_campaign_extra` | object:10 |
| `$.data.campaign[].seller_campaign_extra.max_commission_rate` | string:10 |
| `$.data.campaign[].seller_campaign_extra.max_open_plan_commission_delta` | string:6 |
| `$.data.campaign[].seller_campaign_extra.min_commission_rate` | string:10 |
| `$.data.campaign[].seller_campaign_extra.min_open_plan_commission_delta` | string:5 |
| `$.data.campaign[].seller_campaign_extra.sample_availability` | string:10 |
| `$.data.campaign[].seller_info` | object:10 |
| `$.data.campaign[].seller_info.shop_code` | string:10 |
| `$.data.campaign[].seller_info.shop_icon` | object:10 |
| `$.data.campaign[].seller_info.shop_icon.uri` | string:10 |
| `$.data.campaign[].seller_info.shop_icon.url_list` | array:10 |
| `$.data.campaign[].seller_info.shop_icon.url_list[]` | string:20 |
| `$.data.campaign[].seller_info.shop_name` | string:10 |
| `$.data.campaign[].status` | number:10 |
| `$.data.campaign_join_status_counts` | object:1 |
| `$.data.campaign_join_status_counts.1` | number:1 |
| `$.data.campaign_join_status_counts.2` | number:1 |
| `$.data.campaign_join_status_counts.3` | number:1 |
| `$.data.campaign_ongoing_quota_map` | object:1 |
| `$.data.campaign_ongoing_quota_map.1` | object:1 |
| `$.data.campaign_ongoing_quota_map.1.max_count` | number:1 |
| `$.data.campaign_ongoing_quota_map.1.ongoing_count` | number:1 |
| `$.data.status_count` | null:1 |
| `$.data.total_num` | number:1 |
| `$.msg` | string:1 |

## IT / selected

POST `/api/v1/affiliate/partner/product/pick_up/list`；2026-09-13T03:20:21.303793+00:00；单页样本。

| 字段路径 | 类型及出现次数 |
|---|---|
| `$` | object:1 |
| `$.code` | number:1 |
| `$.data` | array:1 |
| `$.data[]` | object:5 |
| `$.data[].campaign_info` | object:5 |
| `$.data[].campaign_info.campaign_id` | string:5 |
| `$.data[].campaign_info.crs_campaign_type` | number:5 |
| `$.data[].campaign_info.name` | string:5 |
| `$.data[].campaign_info.promotion_end_time` | string:5 |
| `$.data[].campaign_info.promotion_start_time` | string:5 |
| `$.data[].campaign_product` | object:5 |
| `$.data[].campaign_product.open_collab_ads_commission_percent` | string:5 |
| `$.data[].campaign_product.plan_commission_percent` | string:5 |
| `$.data[].campaign_product.plan_type` | number:5 |
| `$.data[].campaign_product.product_id` | string:5 |
| `$.data[].campaign_product.product_name` | string:5 |
| `$.data[].campaign_product.product_price` | object:5 |
| `$.data[].campaign_product.product_price.currency` | object:5 |
| `$.data[].campaign_product.product_price.currency.symbol` | string:5 |
| `$.data[].campaign_product.product_price.max_price` | string:5 |
| `$.data[].campaign_product.product_price.min_price` | string:5 |
| `$.data[].campaign_product.product_rating` | number:5 |
| `$.data[].campaign_product.product_regions` | array:4 |
| `$.data[].campaign_product.product_regions[]` | string:22 |
| `$.data[].campaign_product.product_review_count` | string:5 |
| `$.data[].campaign_product.product_sales` | string:5 |
| `$.data[].campaign_product.product_status` | number:5 |
| `$.data[].campaign_product.product_thumbnail` | object:5 |
| `$.data[].campaign_product.product_thumbnail.uri` | string:5 |
| `$.data[].campaign_product.product_thumbnail.url_list` | array:5 |
| `$.data[].campaign_product.product_thumbnail.url_list[]` | string:10 |
| `$.data[].campaign_product.product_type` | string:2 |
| `$.data[].campaign_product.shop_experience_score` | number:5 |
| `$.data[].campaign_product.shop_name` | string:5 |
| `$.data[].campaign_product.shop_score` | string:5 |
| `$.data[].campaign_product.stock` | string:5 |
| `$.data[].campaign_product.total_commission_percent` | string:5 |
| `$.data[].has_free_sample` | boolean:5 |
| `$.data[].product_source` | number:5 |
| `$.msg` | string:1 |
| `$.total_num` | number:1 |

## IT / members

GET `/api/v1/affiliate/partner/campaign/product_list/products`；2026-09-13T03:20:23.096120+00:00；单页样本。

| 字段路径 | 类型及出现次数 |
|---|---|
| `$` | object:1 |
| `$.code` | number:1 |
| `$.data` | object:1 |
| `$.data.campaign_products` | array:1 |
| `$.data.campaign_products[]` | object:1 |
| `$.data.campaign_products[].campaign_id` | string:1 |
| `$.data.campaign_products[].creator_commission_percent` | string:1 |
| `$.data.campaign_products[].crs_campaign_type` | number:1 |
| `$.data.campaign_products[].is_register_again` | boolean:1 |
| `$.data.campaign_products[].marked` | boolean:1 |
| `$.data.campaign_products[].open_collab_ads_commission_percent` | string:1 |
| `$.data.campaign_products[].partner_commission_percent` | string:1 |
| `$.data.campaign_products[].plan_commission_percent` | string:1 |
| `$.data.campaign_products[].plan_type` | number:1 |
| `$.data.campaign_products[].product_id` | string:1 |
| `$.data.campaign_products[].product_label` | object:1 |
| `$.data.campaign_products[].product_label.deboost_code` | number:1 |
| `$.data.campaign_products[].product_name` | string:1 |
| `$.data.campaign_products[].product_price` | object:1 |
| `$.data.campaign_products[].product_price.max_price` | string:1 |
| `$.data.campaign_products[].product_price.min_price` | string:1 |
| `$.data.campaign_products[].product_regions` | array:1 |
| `$.data.campaign_products[].product_regions[]` | string:7 |
| `$.data.campaign_products[].product_sales` | string:1 |
| `$.data.campaign_products[].product_status` | number:1 |
| `$.data.campaign_products[].product_thumbnail` | object:1 |
| `$.data.campaign_products[].product_thumbnail.uri` | string:1 |
| `$.data.campaign_products[].product_thumbnail.url_list` | array:1 |
| `$.data.campaign_products[].product_thumbnail.url_list[]` | string:2 |
| `$.data.campaign_products[].sample_quota` | number:1 |
| `$.data.campaign_products[].seller_id` | string:1 |
| `$.data.campaign_products[].shop_code` | string:1 |
| `$.data.campaign_products[].shop_experience_score` | number:1 |
| `$.data.campaign_products[].shop_icon` | object:1 |
| `$.data.campaign_products[].shop_icon.uri` | string:1 |
| `$.data.campaign_products[].shop_icon.url_list` | array:1 |
| `$.data.campaign_products[].shop_icon.url_list[]` | string:2 |
| `$.data.campaign_products[].shop_name` | string:1 |
| `$.data.campaign_products[].stock` | string:1 |
| `$.data.campaign_products[].total_commission_percent` | string:1 |
| `$.data.next_cursor` | number:1 |
| `$.data.total_num` | number:1 |
| `$.msg` | string:1 |

## BR / campaigns

GET `/api/v1/affiliate/partner/campaign/list`；2026-09-13T03:20:24.707356+00:00；单页样本。

| 字段路径 | 类型及出现次数 |
|---|---|
| `$` | object:1 |
| `$.code` | number:1 |
| `$.data` | object:1 |
| `$.data.campaign` | array:1 |
| `$.data.campaign[]` | object:10 |
| `$.data.campaign[].campaign_id` | string:10 |
| `$.data.campaign[].campaign_statistics` | object:10 |
| `$.data.campaign[].campaign_statistics.applied_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.closed_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.joined_seller_num` | number:10 |
| `$.data.campaign[].campaign_statistics.ongoing_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.pending_closed_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.pending_product_num` | number:10 |
| `$.data.campaign[].campaign_statistics.rejected_product_num` | number:10 |
| `$.data.campaign[].campaign_type` | number:10 |
| `$.data.campaign[].campaign_version` | number:10 |
| `$.data.campaign[].commission` | string:10 |
| `$.data.campaign[].crs_campaign_type` | number:10 |
| `$.data.campaign[].description` | string:10 |
| `$.data.campaign[].is_locked` | boolean:10 |
| `$.data.campaign[].main_campaign_id` | string:10 |
| `$.data.campaign[].name` | string:10 |
| `$.data.campaign[].new_products_count` | number:10 |
| `$.data.campaign[].partner_join_status` | number:10 |
| `$.data.campaign[].promotion_end_time` | string:10 |
| `$.data.campaign[].promotion_start_time` | string:10 |
| `$.data.campaign[].region` | string:10 |
| `$.data.campaign[].registration_end_time` | string:10 |
| `$.data.campaign[].registration_start_time` | string:10 |
| `$.data.campaign[].sample_budget` | object:10 |
| `$.data.campaign[].seller_campaign_extra` | object:10 |
| `$.data.campaign[].seller_campaign_extra.max_commission_rate` | string:10 |
| `$.data.campaign[].seller_campaign_extra.max_open_plan_commission_delta` | string:10 |
| `$.data.campaign[].seller_campaign_extra.min_commission_rate` | string:10 |
| `$.data.campaign[].seller_campaign_extra.min_open_plan_commission_delta` | string:10 |
| `$.data.campaign[].seller_campaign_extra.sample_availability` | string:10 |
| `$.data.campaign[].seller_info` | object:10 |
| `$.data.campaign[].seller_info.shop_code` | string:10 |
| `$.data.campaign[].seller_info.shop_icon` | object:10 |
| `$.data.campaign[].seller_info.shop_icon.uri` | string:10 |
| `$.data.campaign[].seller_info.shop_icon.url_list` | array:10 |
| `$.data.campaign[].seller_info.shop_icon.url_list[]` | string:20 |
| `$.data.campaign[].seller_info.shop_name` | string:10 |
| `$.data.campaign[].status` | number:10 |
| `$.data.campaign_join_status_counts` | object:1 |
| `$.data.campaign_join_status_counts.1` | number:1 |
| `$.data.campaign_join_status_counts.2` | number:1 |
| `$.data.campaign_join_status_counts.3` | number:1 |
| `$.data.campaign_ongoing_quota_map` | object:1 |
| `$.data.campaign_ongoing_quota_map.1` | object:1 |
| `$.data.campaign_ongoing_quota_map.1.max_count` | number:1 |
| `$.data.campaign_ongoing_quota_map.1.ongoing_count` | number:1 |
| `$.data.status_count` | null:1 |
| `$.data.total_num` | number:1 |
| `$.msg` | string:1 |

## BR / selected

POST `/api/v1/affiliate/partner/product/pick_up/list`；2026-09-13T03:20:26.978342+00:00；单页样本。

| 字段路径 | 类型及出现次数 |
|---|---|
| `$` | object:1 |
| `$.code` | number:1 |
| `$.data` | array:1 |
| `$.data[]` | object:5 |
| `$.data[].campaign_info` | object:5 |
| `$.data[].campaign_info.campaign_id` | string:5 |
| `$.data[].campaign_info.contact_info` | object:5 |
| `$.data[].campaign_info.contact_info.campaign_contact_info` | array:5 |
| `$.data[].campaign_info.contact_info.campaign_contact_info[]` | object:30 |
| `$.data[].campaign_info.contact_info.campaign_contact_info[].region_code` | string:25 |
| `$.data[].campaign_info.contact_info.campaign_contact_info[].type` | number:30 |
| `$.data[].campaign_info.contact_info.campaign_contact_info[].value` | string:30 |
| `$.data[].campaign_info.contact_info.email` | string:5 |
| `$.data[].campaign_info.contact_info.phone` | string:5 |
| `$.data[].campaign_info.contact_info.phone_region_code` | string:5 |
| `$.data[].campaign_info.contact_info.whatsapp` | string:5 |
| `$.data[].campaign_info.contact_info.whatsapp_region_code` | string:5 |
| `$.data[].campaign_info.crs_campaign_type` | number:5 |
| `$.data[].campaign_info.name` | string:5 |
| `$.data[].campaign_info.promotion_end_time` | string:5 |
| `$.data[].campaign_info.promotion_start_time` | string:5 |
| `$.data[].campaign_product` | object:5 |
| `$.data[].campaign_product.open_collab_ads_commission_percent` | string:3 |
| `$.data[].campaign_product.plan_commission_percent` | string:5 |
| `$.data[].campaign_product.plan_type` | number:5 |
| `$.data[].campaign_product.product_id` | string:5 |
| `$.data[].campaign_product.product_name` | string:5 |
| `$.data[].campaign_product.product_price` | object:5 |
| `$.data[].campaign_product.product_price.currency` | object:5 |
| `$.data[].campaign_product.product_price.currency.symbol` | string:5 |
| `$.data[].campaign_product.product_price.max_price` | string:5 |
| `$.data[].campaign_product.product_price.min_price` | string:5 |
| `$.data[].campaign_product.product_rating` | number:5 |
| `$.data[].campaign_product.product_review_count` | string:5 |
| `$.data[].campaign_product.product_sales` | string:5 |
| `$.data[].campaign_product.product_status` | number:5 |
| `$.data[].campaign_product.product_thumbnail` | object:5 |
| `$.data[].campaign_product.product_thumbnail.uri` | string:5 |
| `$.data[].campaign_product.product_thumbnail.url_list` | array:5 |
| `$.data[].campaign_product.product_thumbnail.url_list[]` | string:10 |
| `$.data[].campaign_product.shop_experience_score` | number:5 |
| `$.data[].campaign_product.shop_name` | string:5 |
| `$.data[].campaign_product.shop_score` | string:5 |
| `$.data[].campaign_product.stock` | string:5 |
| `$.data[].campaign_product.total_commission_percent` | string:5 |
| `$.data[].has_free_sample` | boolean:5 |
| `$.data[].product_source` | number:5 |
| `$.msg` | string:1 |
| `$.total_num` | number:1 |

## IT / samples

仅成功响应及data.total，未返回样品明细；不证明行字段兼容。

| 字段路径 | 类型及出现次数 |
|---|---|
| `$` | object:1 |
| `$.code` | number:1 |
| `$.data` | object:1 |
| `$.data.total` | number:1 |
| `$.msg` | string:1 |

## BR / samples

仅成功响应及data.total，未返回样品明细；不证明行字段兼容。

| 字段路径 | 类型及出现次数 |
|---|---|
| `$` | object:1 |
| `$.code` | number:1 |
| `$.data` | object:1 |
| `$.data.total` | number:1 |
| `$.msg` | string:1 |
