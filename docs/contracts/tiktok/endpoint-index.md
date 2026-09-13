# 接口路径目录

与[业务用途目录](business-catalog.md)使用同一注册表生成。GET也可能写入；未知副作用不得调用。

| ID | 方法 | 路径 | 副作用 | 读取证据 |
|---|---|---|---|---|
| TK-001 | POST | `/api/v1/affiliate/lux/product/category/childrenv2` | business_read | it：拒绝/未验收 |
| TK-002 | GET | `/api/v1/affiliate/partner/campaign/list` | business_read | mx：成功读取；it：成功读取；br：成功读取 |
| TK-003 | GET | `/api/v1/affiliate/partner/campaign/product/link` | external_write | 源码/历史脚本；未重验 |
| TK-004 | GET | `/api/v1/affiliate/partner/campaign/product/list` | business_read | 源码/历史脚本；未重验 |
| TK-005 | POST | `/api/v1/affiliate/partner/campaign/product_list/create` | external_write | 源码/历史脚本；未重验 |
| TK-006 | POST | `/api/v1/affiliate/partner/campaign/product_list/delete` | external_write | 源码/历史脚本；未重验 |
| TK-007 | GET | `/api/v1/affiliate/partner/campaign/product_list/list` | business_read | it：成功读取 |
| TK-008 | GET | `/api/v1/affiliate/partner/campaign/product_list/products` | business_read | it：成功读取 |
| TK-009 | POST | `/api/v1/affiliate/partner/campaign/review` | external_write | 源码/历史脚本；未重验 |
| TK-010 | POST | `/api/v1/affiliate/partner/campaign/sample/budget/review` | external_write | 源码/历史脚本；未重验 |
| TK-011 | GET | `/api/v1/affiliate/partner/campaign/sample/price` | unverified | 源码/历史脚本；未重验 |
| TK-012 | POST | `/api/v1/affiliate/partner/campaign/seller_requested/review` | external_write | 源码/历史脚本；未重验 |
| TK-013 | POST | `/api/v1/affiliate/partner/im/filter/creator/mget` | business_read | mx：成功读取 |
| TK-014 | GET | `/api/v1/affiliate/partner/im/id/get` | business_read | 源码/历史脚本；未重验 |
| TK-015 | GET | `/api/v1/affiliate/partner/im/product_list/list` | business_read | it：成功读取 |
| TK-016 | GET | `/api/v1/affiliate/partner/im/token/get` | business_read | 源码/历史脚本；未重验 |
| TK-017 | GET | `/api/v1/affiliate/partner/info` | business_read | 源码/历史脚本；未重验 |
| TK-018 | GET | `/api/v1/affiliate/partner/product/opportunity_product/campaign_detail` | business_read | it：成功读取 |
| TK-019 | POST | `/api/v1/affiliate/partner/product/opportunity_product/list` | business_read | it：成功读取 |
| TK-020 | POST | `/api/v1/affiliate/partner/product/pick_up/list` | business_read | mx：成功读取；it：成功读取；br：成功读取 |
| TK-021 | POST | `/api/v1/affiliate/partner/product/pick_up/select` | external_write | 源码/历史脚本；未重验 |
| TK-022 | GET | `/api/v1/affiliate/partner/relation/list` | business_read | mx：成功读取 |
| TK-023 | POST | `/api/v1/affiliate/partner/sample/records/list` | business_read | mx：成功读取；it：成功读取（无明细）；br：成功读取（无明细） |
| TK-024 | POST | `/api/v1/im/conversation/create` | external_write | 源码/历史脚本；未重验 |
| TK-025 | GET | `/api/v1/insights/export/file/` | business_read | 源码/历史脚本；未重验 |
| TK-026 | POST | `/api/v1/insights/partner/report/data/list/export` | remote_export_job | 源码/历史脚本；未重验 |
| TK-027 | POST | `/api/v1/oec/affiliate/creator/marketplace/4partner/find` | business_read | 源码/历史脚本；未重验 |
| TK-028 | POST | `/api/v1/oec/affiliate/creator/marketplace/4partner/profile` | business_read | 源码/历史脚本；未重验 |
| TK-029 | POST | `/v1/message/get_by_conversation` | business_read | 源码/历史脚本；未重验 |
| TK-030 | POST | `/v1/message/get_by_id` | business_read | 源码/历史脚本；未重验 |
| TK-031 | POST | `/v1/message/send` | external_write | 源码/历史脚本；未重验 |
| TK-032 | POST | `/v2/conversation/get_info` | business_read | 源码/历史脚本；未重验 |
| TK-033 | POST | `/v2/message/get_by_user_init` | business_read | 源码/历史脚本；未重验 |
| TK-034 | GET | `/api/v1/affiliate/partner/im/collaboration/get` | business_read | mx：成功读取；mx：成功读取；mx：成功读取 |

机器可读用途、参数、数据及来源见[注册表](endpoint-registry.json)。历史SDK候选独立见[网页脚本发现](web-discoveries.md)，不混入已验证能力。
