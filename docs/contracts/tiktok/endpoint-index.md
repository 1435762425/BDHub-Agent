# 接口路径目录

方法来自源码调用；unverified表示仅发现常量或响应捕获，未确认可复用请求。业务写入与导出作业本轮均未执行。前缀路径不是一个完整请求。

| ID | 方法 | 路径 | 副作用 | 本轮读取证据 |
|---|---|---|---|---|
| TK-001 | POST | `/api/v1/affiliate/lux/product/category/childrenv2` | business_read | 源码，未重验 |
| TK-002 | GET | `/api/v1/affiliate/partner/campaign/list` | business_read | mx:observed, it:observed, br:observed |
| TK-003 | POST | `/api/v1/affiliate/partner/campaign/product/link` | external_write | 源码，未重验 |
| TK-004 | GET | `/api/v1/affiliate/partner/campaign/product/list` | business_read | 源码，未重验 |
| TK-005 | POST | `/api/v1/affiliate/partner/campaign/product_list/create` | external_write | 源码，未重验 |
| TK-006 | POST | `/api/v1/affiliate/partner/campaign/product_list/delete` | external_write | 源码，未重验 |
| TK-007 | GET | `/api/v1/affiliate/partner/campaign/product_list/list` | business_read | 源码，未重验 |
| TK-008 | GET | `/api/v1/affiliate/partner/campaign/product_list/products` | business_read | it:observed |
| TK-009 | POST | `/api/v1/affiliate/partner/campaign/review` | external_write | 源码，未重验 |
| TK-010 | POST | `/api/v1/affiliate/partner/campaign/sample/budget/review` | external_write | 源码，未重验 |
| TK-011 | unverified | `/api/v1/affiliate/partner/campaign/sample/price` | unverified | 源码，未重验 |
| TK-012 | POST | `/api/v1/affiliate/partner/campaign/seller_requested/review` | external_write | 源码，未重验 |
| TK-013 | unverified | `/api/v1/affiliate/partner/im/filter/creator/mget` | unverified | 源码，未重验 |
| TK-014 | GET | `/api/v1/affiliate/partner/im/id/get` | business_read | 源码，未重验 |
| TK-015 | GET | `/api/v1/affiliate/partner/im/product_list/list` | business_read | 源码，未重验 |
| TK-016 | GET | `/api/v1/affiliate/partner/im/token/get` | business_read | 源码，未重验 |
| TK-017 | GET | `/api/v1/affiliate/partner/info` | business_read | 源码，未重验 |
| TK-018 | GET | `/api/v1/affiliate/partner/product/opportunity_product/campaign_detail` | business_read | 源码，未重验 |
| TK-019 | POST | `/api/v1/affiliate/partner/product/opportunity_product/list` | business_read | 源码，未重验 |
| TK-020 | POST | `/api/v1/affiliate/partner/product/pick_up/list` | business_read | mx:observed, it:observed, br:observed |
| TK-021 | POST | `/api/v1/affiliate/partner/product/pick_up/select` | external_write | 源码，未重验 |
| TK-022 | GET | `/api/v1/affiliate/partner/relation/list` | business_read | 源码，未重验 |
| TK-023 | POST | `/api/v1/affiliate/partner/sample/records/list` | business_read | mx:observed；it/br:成功但无明细 |
| TK-024 | POST | `/api/v1/im/conversation/create` | external_write | 源码，未重验 |
| TK-025 | GET | `/api/v1/insights/export/file/` | business_read | 源码，未重验 |
| TK-026 | POST | `/api/v1/insights/partner/report/data/list/export` | remote_export_job | 源码，未重验 |
| TK-027 | POST | `/api/v1/oec/affiliate/creator/marketplace/4partner/find` | business_read | 源码，未重验 |
| TK-028 | POST | `/api/v1/oec/affiliate/creator/marketplace/4partner/profile` | business_read | 源码，未重验 |
| TK-029 | POST | `/v1/message/get_by_conversation` | business_read | 源码，未重验 |
| TK-030 | POST | `/v1/message/get_by_id` | business_read | 源码，未重验 |
| TK-031 | POST | `/v1/message/send` | external_write | 源码，未重验 |
| TK-032 | POST | `/v2/conversation/get_info` | business_read | 源码，未重验 |
| TK-033 | POST | `/v2/message/get_by_user_init` | business_read | 源码，未重验 |

源码位置/行号和机器可读状态见 [endpoint-registry.json](endpoint-registry.json)。稳定源指纹及候选字段见 [source-inventory.json](source-inventory.json)。
