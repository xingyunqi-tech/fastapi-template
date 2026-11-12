# 与 nestjs-template 的契约差异

本文件由 `tests/contract/test_openapi_contract.py` 生成（`UPDATE_CONTRACT_DOC=1 make test-contract`），与实际不符时测试失败。

## 基线中未实现的接口（217 个）

请求这些接口返回 404 信封 `Cannot <METHOD> <path>`。

| 方法 | 路径 | operationId | tag |
|---|---|---|---|
| GET | `/api/v1/users` | getUsers | Users |
| POST | `/api/v1/users` | createUser | Users |
| GET | `/api/v1/users/{id}` | getUser | Users |
| PUT | `/api/v1/users/{id}` | updateUser | Users |
| DELETE | `/api/v1/users/{id}` | deleteUser | Users |
| POST | `/api/v1/files/upload` | uploadFile | Files |
| POST | `/api/v1/auth/email/login` | loginWithEmail | Auth |
| POST | `/api/v1/auth/email/register` | registerWithEmail | Auth |
| POST | `/api/v1/auth/email/confirm` | confirmEmail | Auth |
| POST | `/api/v1/auth/email/confirm/new` | confirmNewEmail | Auth |
| POST | `/api/v1/auth/forgot/password` | forgotPassword | Auth |
| POST | `/api/v1/auth/reset/password` | resetPassword | Auth |
| GET | `/api/v1/auth/me` | getCurrentUser | Auth |
| PUT | `/api/v1/auth/me` | updateCurrentUser | Auth |
| DELETE | `/api/v1/auth/me` | deleteCurrentUser | Auth |
| POST | `/api/v1/auth/refresh` | refreshToken | Auth |
| GET | `/api/v1/auth/sessions` | getMySessions | Auth |
| DELETE | `/api/v1/auth/sessions/{id}` | revokeMySession | Auth |
| POST | `/api/v1/auth/sessions/revoke-others` | revokeOtherSessions | Auth |
| POST | `/api/v1/auth/logout` | logout | Auth |
| POST | `/api/v1/auth/phone/send-code` | sendSmsCode | Auth |
| POST | `/api/v1/auth/phone/login` | loginWithPhone | Auth |
| POST | `/api/v1/auth/phone/sms-login` | loginWithSms | Auth |
| POST | `/api/v1/auth/phone/register` | registerWithPhone | Auth |
| POST | `/api/v1/auth/phone/reset-password` | resetPasswordByPhone | Auth |
| PUT | `/api/v1/auth/password` | changePassword | Auth |
| POST | `/api/v1/auth/wechat/login` | loginWithWechat | Auth |
| POST | `/api/v1/auth/wechat/phone-login` | loginWithWechatPhone | Auth |
| POST | `/api/v1/auth/alipay/login` | loginWithAlipay | Auth |
| GET | `/api/v1/notifications` | getNotifications | Notifications |
| GET | `/api/v1/notifications/unread-count` | getUnreadCount | Notifications |
| GET | `/api/v1/notifications/settings` | getNotificationSettings | Notifications |
| PUT | `/api/v1/notifications/settings` | updateNotificationSettings | Notifications |
| GET | `/api/v1/notifications/{id}` | getNotification | Notifications |
| DELETE | `/api/v1/notifications/{id}` | deleteNotification | Notifications |
| POST | `/api/v1/notifications/read-all` | markAllNotificationsAsRead | Notifications |
| POST | `/api/v1/notifications/{id}/read` | markNotificationAsRead | Notifications |
| GET | `/api/v1/health` | healthCheck | Health |
| GET | `/api/v1/health/live` | livenessProbe | Health |
| GET | `/api/v1/health/ready` | readinessProbe | Health |
| GET | `/api/v1/addresses` | getAddresses | Addresses |
| POST | `/api/v1/addresses` | createAddress | Addresses |
| GET | `/api/v1/addresses/default` | getDefaultAddress | Addresses |
| GET | `/api/v1/addresses/{id}` | getAddress | Addresses |
| PUT | `/api/v1/addresses/{id}` | updateAddress | Addresses |
| DELETE | `/api/v1/addresses/{id}` | deleteAddress | Addresses |
| PUT | `/api/v1/addresses/{id}/default` | setDefaultAddress | Addresses |
| GET | `/api/v1/regions/provinces` | getProvinces | Regions |
| GET | `/api/v1/regions/cities/{provinceCode}` | getCities | Regions |
| GET | `/api/v1/regions/districts/{cityCode}` | getDistricts | Regions |
| GET | `/api/v1/regions/code/{code}` | getRegionByCode | Regions |
| GET | `/api/v1/regions/tree` | getRegionTree | Regions |
| GET | `/api/v1/regions` | queryRegions | Regions |
| GET | `/api/v1/favorites` | getFavorites | Favorites |
| POST | `/api/v1/favorites` | createFavorite | Favorites |
| POST | `/api/v1/favorites/check` | checkFavorite | Favorites |
| POST | `/api/v1/favorites/toggle` | toggleFavorite | Favorites |
| DELETE | `/api/v1/favorites/{id}` | deleteFavorite | Favorites |
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
| GET | `/api/v1/config/{key}` | getConfig | Config |
| PUT | `/api/v1/config/{key}` | upsertConfig | Config |
| GET | `/api/v1/config/admin/{key}` | getConfigAsAdmin | Config |
| GET | `/api/v1/feedbacks` | getFeedbacks | Feedbacks |
| POST | `/api/v1/feedbacks` | createFeedback | Feedbacks |
| GET | `/api/v1/feedbacks/{id}` | getFeedback | Feedbacks |
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
| GET | `/api/v1/me/consents` | getMyConsents | Consents |
| POST | `/api/v1/me/consents` | agreeConsent | Consents |
| POST | `/api/v1/admin/users/{id}/cleanup` | adminRetriggerAccountCleanup | Admin Account Cleanup |
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
| GET | `/api/v1/app-profile` | getAppProfile | App Profile |
| PUT | `/api/v1/admin/app-profile` | adminUpdateAppProfile | Admin App Profile |
| POST | `/api/v1/game/auth/login` | gameLogin | Game Auth |
| POST | `/api/v1/game/auth/refresh` | gameRefreshToken | Game Auth |

## 白名单（实现与基线不同）

- 无
