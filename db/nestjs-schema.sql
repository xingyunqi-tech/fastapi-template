column account_cleanup.completedAt timestamp with time zone NOT NULL
column account_cleanup.userId integer NOT NULL
column address.address character varying(200) NOT NULL
column address.city character varying(50) NOT NULL
column address.cityCode character varying(12) NULL COMMENT 'City region code'
column address.createdAt timestamp without time zone NOT NULL DEFAULT now()
column address.deletedAt timestamp without time zone NULL
column address.district character varying(50) NOT NULL
column address.districtCode character varying(12) NULL COMMENT 'District region code'
column address.id integer NOT NULL DEFAULT nextval('address_id_seq'::regclass)
column address.isDefault boolean NOT NULL DEFAULT false
column address.name character varying(50) NOT NULL
column address.phone character varying(20) NOT NULL
column address.province character varying(50) NOT NULL
column address.provinceCode character varying(12) NULL COMMENT 'Province region code'
column address.updatedAt timestamp without time zone NOT NULL DEFAULT now()
column address.userId integer NOT NULL
column admin_alert.acknowledgedAt timestamp without time zone NULL
column admin_alert.createdAt timestamp without time zone NOT NULL DEFAULT now()
column admin_alert.dedupKey character varying(200) NOT NULL
column admin_alert.id integer NOT NULL DEFAULT nextval('admin_alert_id_seq'::regclass)
column admin_alert.level character varying(20) NOT NULL
column admin_alert.message text NOT NULL
column admin_alert.payload jsonb NULL
column admin_alert.type character varying(50) NOT NULL
column admin_alert.updatedAt timestamp without time zone NOT NULL DEFAULT now()
column audit_logs.action character varying(50) NOT NULL
column audit_logs.createdAt timestamp without time zone NOT NULL DEFAULT now()
column audit_logs.entityId character varying NOT NULL
column audit_logs.entityType character varying(100) NOT NULL
column audit_logs.id integer NOT NULL DEFAULT nextval('audit_logs_id_seq'::regclass)
column audit_logs.ipAddress character varying NULL
column audit_logs.newValue jsonb NULL
column audit_logs.oldValue jsonb NULL
column audit_logs.requestId character varying NULL
column audit_logs.userAgent text NULL
column audit_logs.userId character varying NULL
column config.createdAt timestamp without time zone NOT NULL DEFAULT now()
column config.isPublic boolean NOT NULL DEFAULT false
column config.key character varying(200) NOT NULL
column config.updatedAt timestamp without time zone NOT NULL DEFAULT now()
column config.value jsonb NOT NULL
column favorite.createdAt timestamp without time zone NOT NULL DEFAULT now()
column favorite.extra text NULL
column favorite.id integer NOT NULL DEFAULT nextval('favorite_id_seq'::regclass)
column favorite.image character varying(500) NULL
column favorite.subjectId uuid NULL
column favorite.targetId character varying(100) NOT NULL
column favorite.targetType character varying(20) NOT NULL
column favorite.title character varying(200) NULL
column favorite.userId integer NOT NULL
column feedback.contact character varying(100) NULL
column feedback.content text NOT NULL
column feedback.createdAt timestamp without time zone NOT NULL DEFAULT now()
column feedback.errataId uuid NULL
column feedback.id integer NOT NULL DEFAULT nextval('feedback_id_seq'::regclass)
column feedback.images jsonb NULL
column feedback.messageId uuid NULL
column feedback.messageSnapshot jsonb NULL
column feedback.questionId uuid NULL
column feedback.questionVersion integer NULL
column feedback.reason character varying(20) NULL
column feedback.reply text NULL
column feedback.status character varying(20) NOT NULL DEFAULT 'pending'::character varying
column feedback.type character varying(20) NOT NULL
column feedback.updatedAt timestamp without time zone NOT NULL DEFAULT now()
column feedback.userId integer NULL
column file.id uuid NOT NULL DEFAULT uuid_generate_v4()
column file.mimeType character varying(100) NULL
column file.ownerUserId integer NULL
column file.path character varying NOT NULL
column file.sha256 character varying(64) NULL
column file.size integer NULL
column file.visibility character varying(10) NOT NULL DEFAULT 'PUBLIC'::character varying
column notification.category character varying(20) NOT NULL
column notification.channels jsonb NOT NULL DEFAULT '[]'::jsonb
column notification.content text NOT NULL
column notification.createdAt timestamp without time zone NOT NULL DEFAULT now()
column notification.deletedAt timestamp without time zone NULL
column notification.id integer NOT NULL DEFAULT nextval('notification_id_seq'::regclass)
column notification.isRead boolean NOT NULL DEFAULT false
column notification.metadata jsonb NULL
column notification.readAt timestamp without time zone NULL
column notification.sentChannels jsonb NOT NULL DEFAULT '{}'::jsonb
column notification.title character varying(200) NOT NULL
column notification.type character varying(20) NOT NULL
column notification.updatedAt timestamp without time zone NOT NULL DEFAULT now()
column notification.userId integer NOT NULL
column notification_setting.category character varying(20) NOT NULL
column notification_setting.createdAt timestamp without time zone NOT NULL DEFAULT now()
column notification_setting.enableEmail boolean NOT NULL DEFAULT true
column notification_setting.enableInApp boolean NOT NULL DEFAULT true
column notification_setting.enablePush boolean NOT NULL DEFAULT true
column notification_setting.enableSms boolean NOT NULL DEFAULT true
column notification_setting.id integer NOT NULL DEFAULT nextval('notification_setting_id_seq'::regclass)
column notification_setting.updatedAt timestamp without time zone NOT NULL DEFAULT now()
column notification_setting.userId integer NOT NULL
column permission.action character varying(50) NOT NULL
column permission.createdAt timestamp without time zone NOT NULL DEFAULT now()
column permission.description character varying(255) NULL
column permission.id character varying(50) NOT NULL
column permission.name character varying(100) NOT NULL
column permission.resource character varying(50) NOT NULL
column permission.updatedAt timestamp without time zone NOT NULL DEFAULT now()
column region.code character varying(12) NOT NULL
column region.createdAt timestamp without time zone NOT NULL DEFAULT now()
column region.id integer NOT NULL DEFAULT nextval('region_id_seq'::regclass)
column region.level integer NOT NULL
column region.name character varying(50) NOT NULL
column region.parentCode character varying(12) NULL
column region.sort integer NOT NULL DEFAULT 0
column region.updatedAt timestamp without time zone NOT NULL DEFAULT now()
column role.id integer NOT NULL
column role.name character varying NOT NULL
column role_permission.createdAt timestamp without time zone NOT NULL DEFAULT now()
column role_permission.permission_id character varying(50) NOT NULL
column role_permission.role_id integer NOT NULL
column session.createdAt timestamp without time zone NOT NULL DEFAULT now()
column session.deletedAt timestamp without time zone NULL
column session.hash character varying NOT NULL
column session.id integer NOT NULL DEFAULT nextval('session_id_seq'::regclass)
column session.ip character varying(64) NULL
column session.lastActiveAt timestamp without time zone NULL
column session.updatedAt timestamp without time zone NOT NULL DEFAULT now()
column session.userAgent character varying(512) NULL
column session.userId integer NULL
column status.id integer NOT NULL
column status.name character varying NOT NULL
column user.alipayOpenId character varying NULL
column user.birthday date NULL
column user.createdAt timestamp without time zone NOT NULL DEFAULT now()
column user.deletedAt timestamp without time zone NULL
column user.email character varying NULL
column user.firstName character varying NULL
column user.gender integer NULL DEFAULT 0
column user.id integer NOT NULL DEFAULT nextval('user_id_seq'::regclass)
column user.lastName character varying NULL
column user.nickname character varying NULL
column user.password character varying NULL
column user.phone character varying NULL
column user.photoId uuid NULL
column user.provider character varying NOT NULL DEFAULT 'email'::character varying
column user.roleId integer NULL
column user.statusId integer NULL
column user.updatedAt timestamp without time zone NOT NULL DEFAULT now()
column user.wechatOpenId character varying NULL
column user.wechatUnionId character varying NULL
column user_consent.agreedAt timestamp without time zone NOT NULL DEFAULT now()
column user_consent.id uuid NOT NULL DEFAULT uuid_generate_v4()
column user_consent.ip character varying(45) NULL
column user_consent.type character varying(20) NOT NULL
column user_consent.userId integer NOT NULL
column user_consent.version character varying(50) NOT NULL
comment region 'Administrative division data (Province/City/District)'
constraint account_cleanup.FK_account_cleanup_user FOREIGN KEY ("userId") REFERENCES "user"(id) ON DELETE CASCADE
constraint account_cleanup.PK_account_cleanup PRIMARY KEY ("userId")
constraint address.FK_address_user FOREIGN KEY ("userId") REFERENCES "user"(id) ON DELETE CASCADE
constraint address.PK_address_id PRIMARY KEY (id)
constraint admin_alert.PK_44cf0b17edf69fb3f9b62c1ee7b PRIMARY KEY (id)
constraint audit_logs.PK_audit_logs PRIMARY KEY (id)
constraint config.PK_config_key PRIMARY KEY (key)
constraint favorite.FK_favorite_user FOREIGN KEY ("userId") REFERENCES "user"(id) ON DELETE CASCADE
constraint favorite.PK_favorite_id PRIMARY KEY (id)
constraint favorite.UQ_favorite_user_target UNIQUE ("userId", "targetType", "targetId")
constraint feedback.FK_feedback_errata FOREIGN KEY ("errataId") REFERENCES errata(id) ON DELETE SET NULL
constraint feedback.FK_feedback_question FOREIGN KEY ("questionId") REFERENCES question(id) ON DELETE SET NULL
constraint feedback.FK_feedback_user FOREIGN KEY ("userId") REFERENCES "user"(id) ON DELETE SET NULL
constraint feedback.PK_feedback_id PRIMARY KEY (id)
constraint file.PK_36b46d232307066b3a2c9ea3a1d PRIMARY KEY (id)
constraint notification.FK_notification_user FOREIGN KEY ("userId") REFERENCES "user"(id) ON DELETE CASCADE
constraint notification.PK_notification_id PRIMARY KEY (id)
constraint notification_setting.FK_notification_setting_user FOREIGN KEY ("userId") REFERENCES "user"(id) ON DELETE CASCADE
constraint notification_setting.PK_notification_setting_id PRIMARY KEY (id)
constraint notification_setting.UQ_notification_setting_userId_category UNIQUE ("userId", category)
constraint permission.PK_permission_id PRIMARY KEY (id)
constraint region.PK_region_id PRIMARY KEY (id)
constraint region.UQ_region_code UNIQUE (code)
constraint role.PK_b36bcfe02fc8de3c57a8b2391c2 PRIMARY KEY (id)
constraint role_permission.FK_ROLE_PERMISSION_PERMISSION FOREIGN KEY (permission_id) REFERENCES permission(id) ON DELETE CASCADE
constraint role_permission.FK_ROLE_PERMISSION_ROLE FOREIGN KEY (role_id) REFERENCES role(id) ON DELETE CASCADE
constraint role_permission.PK_role_permission PRIMARY KEY (role_id, permission_id)
constraint session.FK_3d2f174ef04fb312fdebd0ddc53 FOREIGN KEY ("userId") REFERENCES "user"(id)
constraint session.PK_f55da76ac1c3ac420f444d2ff11 PRIMARY KEY (id)
constraint status.PK_e12743a7086ec826733f54e1d95 PRIMARY KEY (id)
constraint user.FK_75e2be4ce11d447ef43be0e374f FOREIGN KEY ("photoId") REFERENCES file(id)
constraint user.FK_c28e52f758e7bbc53828db92194 FOREIGN KEY ("roleId") REFERENCES role(id)
constraint user.FK_dc18daa696860586ba4667a9d31 FOREIGN KEY ("statusId") REFERENCES status(id)
constraint user.PK_cace4a159ff9f2512dd42373760 PRIMARY KEY (id)
constraint user.REL_75e2be4ce11d447ef43be0e374 UNIQUE ("photoId")
constraint user.UQ_e12875dfb3b1d92d7d7c5377e22 UNIQUE (email)
constraint user.user_alipayOpenId_key UNIQUE ("alipayOpenId")
constraint user.user_phone_key UNIQUE (phone)
constraint user.user_wechatOpenId_key UNIQUE ("wechatOpenId")
constraint user_consent.FK_user_consent_user FOREIGN KEY ("userId") REFERENCES "user"(id) ON DELETE CASCADE
constraint user_consent.PK_user_consent PRIMARY KEY (id)
index address.IDX_address_cityCode CREATE INDEX "IDX_address_cityCode" ON public.address USING btree ("cityCode")
index address.IDX_address_deletedAt CREATE INDEX "IDX_address_deletedAt" ON public.address USING btree ("deletedAt") WHERE ("deletedAt" IS NULL)
index address.IDX_address_districtCode CREATE INDEX "IDX_address_districtCode" ON public.address USING btree ("districtCode")
index address.IDX_address_provinceCode CREATE INDEX "IDX_address_provinceCode" ON public.address USING btree ("provinceCode")
index address.IDX_address_userId CREATE INDEX "IDX_address_userId" ON public.address USING btree ("userId")
index address.IDX_address_userId_isDefault_createdAt CREATE INDEX "IDX_address_userId_isDefault_createdAt" ON public.address USING btree ("userId", "isDefault" DESC, "createdAt" DESC)
index address.UQ_address_userId_default CREATE UNIQUE INDEX "UQ_address_userId_default" ON public.address USING btree ("userId") WHERE ("isDefault" AND ("deletedAt" IS NULL))
index admin_alert.IDX_admin_alert_createdAt CREATE INDEX "IDX_admin_alert_createdAt" ON public.admin_alert USING btree ("createdAt")
index admin_alert.IDX_admin_alert_type CREATE INDEX "IDX_admin_alert_type" ON public.admin_alert USING btree (type)
index admin_alert.UQ_admin_alert_dedupKey_open CREATE UNIQUE INDEX "UQ_admin_alert_dedupKey_open" ON public.admin_alert USING btree ("dedupKey") WHERE ("acknowledgedAt" IS NULL)
index audit_logs.IDX_audit_logs_action CREATE INDEX "IDX_audit_logs_action" ON public.audit_logs USING btree (action)
index audit_logs.IDX_audit_logs_createdAt CREATE INDEX "IDX_audit_logs_createdAt" ON public.audit_logs USING btree ("createdAt")
index audit_logs.IDX_audit_logs_entityId CREATE INDEX "IDX_audit_logs_entityId" ON public.audit_logs USING btree ("entityId")
index audit_logs.IDX_audit_logs_entityType CREATE INDEX "IDX_audit_logs_entityType" ON public.audit_logs USING btree ("entityType")
index audit_logs.IDX_audit_logs_requestId CREATE INDEX "IDX_audit_logs_requestId" ON public.audit_logs USING btree ("requestId")
index audit_logs.IDX_audit_logs_userId CREATE INDEX "IDX_audit_logs_userId" ON public.audit_logs USING btree ("userId")
index favorite.IDX_favorite_targetId CREATE INDEX "IDX_favorite_targetId" ON public.favorite USING btree ("targetId")
index favorite.IDX_favorite_targetType CREATE INDEX "IDX_favorite_targetType" ON public.favorite USING btree ("targetType")
index favorite.IDX_favorite_userId CREATE INDEX "IDX_favorite_userId" ON public.favorite USING btree ("userId")
index favorite.IDX_favorite_userId_subjectId CREATE INDEX "IDX_favorite_userId_subjectId" ON public.favorite USING btree ("userId", "subjectId")
index favorite.IDX_favorite_userId_targetType CREATE INDEX "IDX_favorite_userId_targetType" ON public.favorite USING btree ("userId", "targetType")
index feedback.IDX_feedback_errataId CREATE INDEX "IDX_feedback_errataId" ON public.feedback USING btree ("errataId")
index feedback.IDX_feedback_questionId CREATE INDEX "IDX_feedback_questionId" ON public.feedback USING btree ("questionId")
index feedback.IDX_feedback_status CREATE INDEX "IDX_feedback_status" ON public.feedback USING btree (status)
index feedback.IDX_feedback_type CREATE INDEX "IDX_feedback_type" ON public.feedback USING btree (type)
index feedback.IDX_feedback_userId CREATE INDEX "IDX_feedback_userId" ON public.feedback USING btree ("userId")
index file.IDX_file_ownerUserId CREATE INDEX "IDX_file_ownerUserId" ON public.file USING btree ("ownerUserId")
index file.IDX_file_path CREATE INDEX "IDX_file_path" ON public.file USING btree (path)
index file.IDX_file_sha256 CREATE INDEX "IDX_file_sha256" ON public.file USING btree (sha256)
index notification.IDX_notification_category CREATE INDEX "IDX_notification_category" ON public.notification USING btree (category)
index notification.IDX_notification_createdAt CREATE INDEX "IDX_notification_createdAt" ON public.notification USING btree ("createdAt")
index notification.IDX_notification_isRead CREATE INDEX "IDX_notification_isRead" ON public.notification USING btree ("isRead")
index notification.IDX_notification_userId CREATE INDEX "IDX_notification_userId" ON public.notification USING btree ("userId")
index notification_setting.IDX_notification_setting_userId CREATE INDEX "IDX_notification_setting_userId" ON public.notification_setting USING btree ("userId")
index permission.IDX_PERMISSION_RESOURCE CREATE INDEX "IDX_PERMISSION_RESOURCE" ON public.permission USING btree (resource)
index region.IDX_region_code CREATE INDEX "IDX_region_code" ON public.region USING btree (code)
index region.IDX_region_level CREATE INDEX "IDX_region_level" ON public.region USING btree (level)
index region.IDX_region_level_parentCode CREATE INDEX "IDX_region_level_parentCode" ON public.region USING btree (level, "parentCode")
index region.IDX_region_parentCode CREATE INDEX "IDX_region_parentCode" ON public.region USING btree ("parentCode")
index role_permission.IDX_ROLE_PERMISSION_PERMISSION CREATE INDEX "IDX_ROLE_PERMISSION_PERMISSION" ON public.role_permission USING btree (permission_id)
index role_permission.IDX_ROLE_PERMISSION_ROLE CREATE INDEX "IDX_ROLE_PERMISSION_ROLE" ON public.role_permission USING btree (role_id)
index session.IDX_3d2f174ef04fb312fdebd0ddc5 CREATE INDEX "IDX_3d2f174ef04fb312fdebd0ddc5" ON public.session USING btree ("userId")
index session.IDX_session_deletedAt CREATE INDEX "IDX_session_deletedAt" ON public.session USING btree ("deletedAt") WHERE ("deletedAt" IS NULL)
index user.IDX_58e4dbff0e1a32a9bdc861bb29 CREATE INDEX "IDX_58e4dbff0e1a32a9bdc861bb29" ON public."user" USING btree ("firstName")
index user.IDX_f0e1b4ecdca13b177e2e3a0613 CREATE INDEX "IDX_f0e1b4ecdca13b177e2e3a0613" ON public."user" USING btree ("lastName")
index user.IDX_user_deletedAt CREATE INDEX "IDX_user_deletedAt" ON public."user" USING btree ("deletedAt") WHERE ("deletedAt" IS NULL)
index user.IDX_user_phone CREATE INDEX "IDX_user_phone" ON public."user" USING btree (phone)
index user.IDX_user_wechatOpenId CREATE INDEX "IDX_user_wechatOpenId" ON public."user" USING btree ("wechatOpenId")
index user.IDX_user_wechatUnionId CREATE INDEX "IDX_user_wechatUnionId" ON public."user" USING btree ("wechatUnionId")
index user_consent.UQ_user_consent_userId_type_version CREATE UNIQUE INDEX "UQ_user_consent_userId_type_version" ON public.user_consent USING btree ("userId", type, version)
