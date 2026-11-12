# 与 nestjs-template 的契约差异

本文件由 `tests/contract/test_openapi_contract.py` 生成（`UPDATE_CONTRACT_DOC=1 make test-contract`），与实际不符时测试失败；「与 nestjs 的行为差异」一节为手写，生成时原样保留。

## 基线中未实现的接口（148 个）

请求这些接口返回 404 信封 `Cannot <METHOD> <path>`。

| 方法 | 路径 | operationId | tag |
|---|---|---|---|
| GET | `/api/v1/practice/sessions` | getPracticeSessions | Practice |
| POST | `/api/v1/practice/sessions` | createPracticeSession | Practice |
| GET | `/api/v1/practice/sessions/ongoing` | getOngoingPracticeSessions | Practice |
| GET | `/api/v1/practice/sessions/{id}` | getPracticeSession | Practice |
| GET | `/api/v1/practice/sessions/{id}/questions` | getPracticeSessionQuestions | Practice |
| PUT | `/api/v1/practice/sessions/{id}/answers` | savePracticeAnswers | Practice |
| POST | `/api/v1/practice/sessions/{id}/self-assessment` | selfAssessPracticeAnswer | Practice |
| POST | `/api/v1/practice/sessions/{id}/progress` | updatePracticeProgress | Practice |
| GET | `/api/v1/practice/sessions/{id}/report` | getPracticeSessionReport | Practice |
| POST | `/api/v1/practice/sessions/{id}/submit` | submitPracticeSession | Practice |
| POST | `/api/v1/practice/sessions/{id}/abandon` | abandonPracticeSession | Practice |
| GET | `/api/v1/questions/{id}` | getQuestion | Questions |
| GET | `/api/v1/exams` | getExams | Exams |
| GET | `/api/v1/exams/{id}/subjects` | getExamSubjects | Exams |
| GET | `/api/v1/me/exam-profiles` | getMyExamProfiles | Exam Profiles |
| PUT | `/api/v1/me/exam-profiles/{examId}` | upsertMyExamProfile | Exam Profiles |
| DELETE | `/api/v1/me/exam-profiles/{examId}` | deleteMyExamProfile | Exam Profiles |
| POST | `/api/v1/me/exam-profiles/{examId}/current` | setMyCurrentExam | Exam Profiles |
| GET | `/api/v1/admin/exams` | adminGetExams | Admin Exams |
| POST | `/api/v1/admin/exams` | adminCreateExam | Admin Exams |
| GET | `/api/v1/admin/exams/{id}` | adminGetExam | Admin Exams |
| PATCH | `/api/v1/admin/exams/{id}` | adminUpdateExam | Admin Exams |
| DELETE | `/api/v1/admin/exams/{id}` | adminDeleteExam | Admin Exams |
| POST | `/api/v1/admin/exams/{id}/publish` | adminPublishExam | Admin Exams |
| POST | `/api/v1/admin/exams/{id}/offline` | adminOfflineExam | Admin Exams |
| GET | `/api/v1/admin/subjects` | adminGetSubjects | Admin Subjects |
| POST | `/api/v1/admin/subjects` | adminCreateSubject | Admin Subjects |
| GET | `/api/v1/admin/subjects/{id}` | adminGetSubject | Admin Subjects |
| PATCH | `/api/v1/admin/subjects/{id}` | adminUpdateSubject | Admin Subjects |
| DELETE | `/api/v1/admin/subjects/{id}` | adminDeleteSubject | Admin Subjects |
| POST | `/api/v1/admin/subjects/{id}/publish` | adminPublishSubject | Admin Subjects |
| POST | `/api/v1/admin/subjects/{id}/offline` | adminOfflineSubject | Admin Subjects |
| GET | `/api/v1/admin/subjects/{id}/knowledge-points` | adminGetSubjectKnowledgePoints | Admin Subjects |
| POST | `/api/v1/admin/knowledge-points` | adminCreateKnowledgePoint | Admin Knowledge Points |
| PATCH | `/api/v1/admin/knowledge-points/{id}` | adminUpdateKnowledgePoint | Admin Knowledge Points |
| DELETE | `/api/v1/admin/knowledge-points/{id}` | adminDeleteKnowledgePoint | Admin Knowledge Points |
| POST | `/api/v1/admin/knowledge-points/{id}/move` | adminMoveKnowledgePoint | Admin Knowledge Points |
| POST | `/api/v1/admin/knowledge-points/{id}/migrate-questions` | adminMigrateKnowledgePointQuestions | Admin Knowledge Points |
| GET | `/api/v1/admin/questions` | adminGetQuestions | Admin Questions |
| POST | `/api/v1/admin/questions` | adminCreateQuestion | Admin Questions |
| POST | `/api/v1/admin/questions/batch-publish` | adminBatchPublishQuestions | Admin Questions |
| POST | `/api/v1/admin/questions/batch-submit` | adminBatchSubmitQuestions | Admin Questions |
| POST | `/api/v1/admin/questions/batch-reject` | adminBatchRejectQuestions | Admin Questions |
| POST | `/api/v1/admin/questions/batch-offline` | adminBatchOfflineQuestions | Admin Questions |
| POST | `/api/v1/admin/questions/batch-delete` | adminBatchDeleteQuestions | Admin Questions |
| PATCH | `/api/v1/admin/questions/batch-update` | adminBatchUpdateQuestions | Admin Questions |
| GET | `/api/v1/admin/questions/{id}` | adminGetQuestion | Admin Questions |
| PATCH | `/api/v1/admin/questions/{id}` | adminUpdateQuestion | Admin Questions |
| DELETE | `/api/v1/admin/questions/{id}` | adminDeleteQuestion | Admin Questions |
| GET | `/api/v1/admin/questions/{id}/versions` | adminGetQuestionVersions | Admin Questions |
| POST | `/api/v1/admin/questions/{id}/submit` | adminSubmitQuestion | Admin Questions |
| POST | `/api/v1/admin/questions/{id}/publish` | adminPublishQuestion | Admin Questions |
| POST | `/api/v1/admin/questions/{id}/reject` | adminRejectQuestion | Admin Questions |
| POST | `/api/v1/admin/questions/{id}/offline` | adminOfflineQuestion | Admin Questions |
| GET | `/api/v1/subjects/{id}/papers` | getSubjectPapers | Papers |
| GET | `/api/v1/papers/{id}` | getPaper | Papers |
| GET | `/api/v1/subjects/{id}/paper-rules` | getSubjectPaperRules | Papers |
| POST | `/api/v1/paper-rules/{id}/generate` | generatePaper | Papers |
| GET | `/api/v1/subjects/{id}/daily` | getSubjectDaily | Papers |
| GET | `/api/v1/subjects/{id}/daily/trial` | getSubjectDailyTrial | Papers |
| POST | `/api/v1/subjects/{id}/daily/trial/check` | checkSubjectDailyTrial | Papers |
| GET | `/api/v1/admin/papers` | adminGetPapers | Admin Papers |
| POST | `/api/v1/admin/papers` | adminCreatePaper | Admin Papers |
| GET | `/api/v1/admin/papers/{id}` | adminGetPaper | Admin Papers |
| PATCH | `/api/v1/admin/papers/{id}` | adminUpdatePaper | Admin Papers |
| DELETE | `/api/v1/admin/papers/{id}` | adminDeletePaper | Admin Papers |
| PUT | `/api/v1/admin/papers/{id}/questions` | adminSetPaperQuestions | Admin Papers |
| POST | `/api/v1/admin/papers/{id}/publish` | adminPublishPaper | Admin Papers |
| POST | `/api/v1/admin/papers/{id}/offline` | adminOfflinePaper | Admin Papers |
| POST | `/api/v1/admin/papers/{id}/copy` | adminCopyPaper | Admin Papers |
| GET | `/api/v1/admin/paper-rules` | adminGetPaperRules | Admin Paper Rules |
| POST | `/api/v1/admin/paper-rules` | adminCreatePaperRule | Admin Paper Rules |
| GET | `/api/v1/admin/paper-rules/{id}` | adminGetPaperRule | Admin Paper Rules |
| PATCH | `/api/v1/admin/paper-rules/{id}` | adminUpdatePaperRule | Admin Paper Rules |
| DELETE | `/api/v1/admin/paper-rules/{id}` | adminDeletePaperRule | Admin Paper Rules |
| POST | `/api/v1/admin/paper-rules/{id}/preview` | adminPreviewPaperRule | Admin Paper Rules |
| GET | `/api/v1/admin/subjects/{id}/daily-sets` | adminGetDailyQuestionSets | Admin Daily Question Sets |
| POST | `/api/v1/admin/subjects/{id}/daily-sets/{date}/regenerate` | adminRegenerateDailyQuestionSet | Admin Daily Question Sets |
| GET | `/api/v1/orders` | getOrders | Orders |
| POST | `/api/v1/orders` | createOrder | Orders |
| GET | `/api/v1/orders/{id}` | getOrder | Orders |
| DELETE | `/api/v1/orders/{id}` | cancelOrder | Orders |
| POST | `/api/v1/wechat/payment/jsapi` | WechatPayController_createJsapiPayment_v1 | Wechat Pay |
| POST | `/api/v1/wechat/payment/app` | WechatPayController_createAppPayment_v1 | Wechat Pay |
| GET | `/api/v1/wechat/order/{outTradeNo}` | WechatPayController_queryOrder_v1 | Wechat Pay |
| POST | `/api/v1/wechat/refund` | WechatPayController_refund_v1 | Wechat Pay |
| POST | `/api/v1/wechat/notify` | WechatPayController_notify_v1 | Wechat Pay |
| POST | `/api/v1/alipay/payment/mobile` | AlipayController_createMobilePayment_v1 | Alipay |
| POST | `/api/v1/alipay/payment/mini` | AlipayController_createMiniPayment_v1 | Alipay |
| POST | `/api/v1/alipay/payment/web` | AlipayController_createWebPayment_v1 | Alipay |
| GET | `/api/v1/alipay/order/{outTradeNo}` | AlipayController_queryOrder_v1 | Alipay |
| POST | `/api/v1/alipay/refund` | AlipayController_refund_v1 | Alipay |
| POST | `/api/v1/alipay/order/close/{outTradeNo}` | AlipayController_closeOrder_v1 | Alipay |
| POST | `/api/v1/alipay/notify` | AlipayController_notify_v1 | Alipay |
| GET | `/api/v1/shares` | getShares | Shares |
| POST | `/api/v1/shares` | createShare | Shares |
| GET | `/api/v1/shares/code/{code}` | getShareByCode | Shares |
| GET | `/api/v1/shares/{id}` | getShareById | Shares |
| DELETE | `/api/v1/shares/{id}` | deleteShare | Shares |
| GET | `/api/v1/shares/{id}/stats` | getShareStats | Shares |
| POST | `/api/v1/shares/code/{code}/track` | trackShare | Shares |
| POST | `/api/v1/shares/wechat/config` | getWeChatConfig | Shares |
| POST | `/api/v1/shares/alipay/config` | getAlipayConfig | Shares |
| GET | `/api/v1/admin/monitor/queues` | getMonitorQueues | Monitor |
| GET | `/api/v1/admin/monitor/alerts` | getMonitorAlerts | Monitor |
| POST | `/api/v1/admin/monitor/alerts/{id}/ack` | acknowledgeMonitorAlert | Monitor |
| GET | `/api/v1/admin/imports` | adminGetImports | Admin Imports |
| POST | `/api/v1/admin/imports` | adminCreateImport | Admin Imports |
| POST | `/api/v1/admin/imports/precheck` | adminPrecheckImport | Admin Imports |
| GET | `/api/v1/admin/imports/{id}` | adminGetImport | Admin Imports |
| GET | `/api/v1/admin/imports/{id}/errors.json` | adminGetImportErrors | Admin Imports |
| GET | `/api/v1/admin/imports/{id}/errors` | adminDownloadImportErrors | Admin Imports |
| GET | `/api/v1/wrong-book` | getWrongBook | Wrong Book |
| GET | `/api/v1/wrong-book/summary` | getWrongBookSummary | Wrong Book |
| POST | `/api/v1/wrong-book/{questionId}/master` | masterWrongQuestion | Wrong Book |
| POST | `/api/v1/wrong-book/{questionId}/restore` | restoreWrongQuestion | Wrong Book |
| GET | `/api/v1/me/study/overview` | getStudyOverview | Study Stats |
| GET | `/api/v1/me/study/daily` | getStudyDaily | Study Stats |
| GET | `/api/v1/me/study/knowledge` | getStudyKnowledge | Study Stats |
| GET | `/api/v1/me/study/weak-points` | getStudyWeakPoints | Study Stats |
| GET | `/api/v1/me/checkins` | getCheckins | Study Stats |
| GET | `/api/v1/subjects/{id}/knowledge-points` | getSubjectKnowledgePoints | Exams |
| PUT | `/api/v1/questions/{id}/note` | saveQuestionNote | Question Notes |
| DELETE | `/api/v1/questions/{id}/note` | deleteQuestionNote | Question Notes |
| GET | `/api/v1/me/question-notes` | getMyQuestionNotes | Question Notes |
| POST | `/api/v1/admin/questions/{id}/errata` | adminCreateErrata | Admin Errata |
| GET | `/api/v1/admin/errata` | adminGetErrataList | Admin Errata |
| GET | `/api/v1/admin/errata/{id}` | adminGetErrata | Admin Errata |
| POST | `/api/v1/admin/errata/{id}/retry` | adminRetryErrata | Admin Errata |
| POST | `/api/v1/questions/{id}/reports` | createQuestionReport | Question Reports |
| GET | `/api/v1/me/question-reports` | getMyQuestionReports | Question Reports |
| GET | `/api/v1/admin/question-reports` | adminGetQuestionReports | Admin Question Reports |
| GET | `/api/v1/admin/question-reports/{id}` | adminGetQuestionReport | Admin Question Reports |
| PATCH | `/api/v1/admin/question-reports/{id}` | adminUpdateQuestionReport | Admin Question Reports |
| GET | `/api/v1/ai/conversations` | getAiConversations | AI |
| POST | `/api/v1/ai/conversations` | createAiConversation | AI |
| GET | `/api/v1/ai/conversations/{id}/messages` | getAiMessages | AI |
| POST | `/api/v1/ai/conversations/{id}/messages` | sendAiMessage | AI |
| DELETE | `/api/v1/ai/conversations/{id}` | deleteAiConversation | AI |
| GET | `/api/v1/ai/messages/{id}` | getAiMessage | AI |
| GET | `/api/v1/ai/messages/{id}/events` | getAiMessageEvents | AI |
| POST | `/api/v1/ai/messages/{id}/stop` | stopAiMessage | AI |
| POST | `/api/v1/ai/messages/{id}/feedback` | setAiMessageFeedback | AI |
| POST | `/api/v1/ai/messages/{id}/report` | reportAiMessage | AI |
| GET | `/api/v1/ai/quota` | getAiQuota | AI |
| GET | `/api/v1/admin/ai/usage` | adminGetAiUsage | Admin AI |
| POST | `/api/v1/game/auth/login` | gameLogin | Game Auth |
| POST | `/api/v1/game/auth/refresh` | gameRefreshToken | Game Auth |

## 白名单（实现与基线不同）

- `POST /api/v1/files/upload`（`FILE_DRIVER=s3-presigned`）：请求体为 `application/json` `FileUploadDto {fileName, fileSize}`，响应为 `{file, uploadSignedUrl}`；local / s3 驱动与基线一致

## 与 nestjs 的行为差异

- 认证邮件（注册、找回密码、改邮箱）在事务提交后异步发送，SMTP 失败不影响接口响应；nestjs 同步发送，失败时接口返回 500。入队超时（2 秒）或失败时在 API 进程内后台直接经 SMTP 发送。
- 队列使用 Taskiq + Redis Stream（键 `taskiq:<queue>`），与 nestjs 的 BullMQ 队列不互通，两个后端的 worker 不能互相消费任务。
- worker 崩溃后未确认的消息由其它 worker 在空闲 10 分钟后经 XAUTOCLAIM 回收，回收只在该 worker 读到新消息后进行（taskiq-redis `listen` 的行为）。
- notification 队列的并发 5 / 限速 10 条每秒在每个 worker 进程内以信号量 + 滑动窗口计数实现，按进程分别计算；nestjs BullMQ 的限速为跨 worker 的全局计数，本服务启用多个 worker 进程时总速率上限为 10 条每秒 × 进程数。重试的退避等待不占用并发与限速名额。
- worker 的 `queue_jobs_total` 须与 API 共用同一 `PROMETHEUS_MULTIPROC_DIR` 才会出现在 API 的 `/metrics`。
- 限流算法不同：nestjs 为固定窗口计数（`throttle:{sha}:default` INCR，超限写 `throttle:{sha}:default:block`），本服务为 GCRA，状态存于 `throttle:{sha}:default:gcra`（哈希输入相同）。两个后端共用 Redis 时各自计数、互不影响，同一客户端在两边的限额分别计算。
- Redis 未启用或故障时限流回落进程内存，限额按 worker 进程独立计算；内存表满 10000 个键时先清除已过期的键，再淘汰最早过期的键。
- 健康检查 `memory_heap` 比较的是进程 RSS，nestjs 比较的是 V8 `heapUsed`；阈值同为 300MB，RSS 包含解释器与原生库内存，通常大于同等负载下的 heapUsed，因此可能更早判定为 down。
- S3 驱动上传对象的 `ContentType` 按文件扩展名设置（未知时 `application/octet-stream`）；nestjs multer-s3 使用 `AUTO_CONTENT_TYPE` 按文件内容嗅探。只影响存储对象的元数据，不影响 API 响应。
- 本期没有 questions / papers 模块：收藏（新增、切换、检查）目标为题目时返回 `questionNotAccessible`，为试卷时返回 `paperNotFound`；收藏列表中这两类收藏的 `targetOffline` 恒为 `true`。与 nestjs 共用数据库时，nestjs 已创建的题目 / 试卷收藏在本服务中显示为已下架。
