import {sql} from 'drizzle-orm';
import {BUDGET_BASIS,BUDGET_REMAINING} from '../lib/budget-query.mjs';
import { sqliteTable, text, integer, primaryKey, index, uniqueIndex, foreignKey, check } from 'drizzle-orm/sqlite-core';
export const jobs = sqliteTable('jobs', {
 id: text('id').primaryKey(), mode: text('mode').notNull(), day: text('day').notNull(),
 state: text('state').notNull(), page: integer('page').notNull(), pageSize: integer('page_size').notNull(),
 total: integer('total'), saved: integer('saved').notNull(), calls: integer('calls').notNull(),
 maxPayload: integer('max_payload').notNull(), lease: text('lease'), leaseUntil: integer('lease_until').notNull(),
 error: text('error'), createdAt: text('created_at').notNull(), updatedAt: text('updated_at').notNull(),
});
export const records = sqliteTable('records', {
 jobId: text('job_id').notNull(), sourceId: text('source_id').notNull(), title: text('title').notNull(),
 agency: text('agency').notNull(), amount: text('amount').notNull(), raw: text('raw').notNull(),
}, t => [primaryKey({columns:[t.jobId,t.sourceId]})]);
export const receipts = sqliteTable('receipts', {
 jobId: text('job_id').notNull(), page: integer('page').notNull(), count: integer('count').notNull(),
 bytes: integer('bytes').notNull(), hash: text('hash').notNull(), committedAt: text('committed_at').notNull(),
}, t => [primaryKey({columns:[t.jobId,t.page]})]);
export const usage = sqliteTable('usage', { day:text('day').primaryKey(), calls:integer('calls').notNull() });

export const collectionSessions=sqliteTable('collection_sessions',{
 id:text('id').primaryKey(),mode:text('mode').notNull(),fromDay:text('from_day').notNull(),toDay:text('to_day').notNull(),state:text('state').notNull(),
 cursor:integer('cursor').notNull(),day:text('day').notNull(),page:integer('page').notNull(),pageSize:integer('page_size').notNull(),sourceTotal:integer('source_total'),fixtureTotal:integer('fixture_total').notNull(),
 fetched:integer('fetched').notNull(),matched:integer('matched').notNull(),excluded:integer('excluded').notNull(),completedDays:integer('completed_days').notNull(),calls:integer('calls').notNull(),maxPayload:integer('max_payload').notNull(),
 lease:text('lease'),leaseUntil:integer('lease_until').notNull(),error:text('error'),createdAt:text('created_at').notNull(),updatedAt:text('updated_at').notNull()
});
export const deliveryRows=sqliteTable('delivery_rows',{
 mode:text('mode').notNull(),sourceKey:text('source_key').notNull(),isActive:integer('is_active').notNull().default(1),inactiveReason:text('inactive_reason').notNull().default(''),changeRank:text('change_rank').notNull().default('0'),vendorBizno:text('vendor_bizno').notNull().default(''),contractNo:text('contract_no').notNull().default(''),sourceDate:text('source_date').notNull(),dateBasis:text('date_basis').notNull(),category:text('category').notNull(),detailCode:text('detail_code').notNull(),detailName:text('detail_name').notNull(),
 requestNo:text('request_no').notNull(),changeOrder:text('change_order').notNull(),sequence:text('sequence').notNull(),requestName:text('request_name').notNull(),finalFlag:text('final_flag').notNull(),
 itemId:text('item_id').notNull(),itemName:text('item_name').notNull(),model:text('model').notNull(),agency:text('agency').notNull(),agencyCode:text('agency_code').notNull(),region:text('region').notNull(),vendor:text('vendor').notNull(),
 quantity:text('quantity'),unitPrice:text('unit_price'),amount:text('amount'),requestTotal:text('request_total'),raw:text('raw').notNull(),lastSession:text('last_session').notNull(),updatedAt:text('updated_at').notNull()
},t=>[primaryKey({columns:[t.mode,t.sourceKey]}),index('idx_delivery_current').on(t.mode,t.isActive,t.requestNo,t.sequence,t.changeRank),index('idx_delivery_scope').on(t.mode,t.sourceDate,t.sourceKey),index('idx_delivery_category').on(t.mode,t.category,t.sourceDate)]);
export const collectionReceipts=sqliteTable('collection_receipts',{
 sessionId:text('session_id').notNull(),day:text('day').notNull(),page:integer('page').notNull(),fetched:integer('fetched').notNull(),matched:integer('matched').notNull(),excluded:integer('excluded').notNull(),bytes:integer('bytes').notNull(),hash:text('hash').notNull(),committedAt:text('committed_at').notNull()
},t=>[primaryKey({columns:[t.sessionId,t.day,t.page]})]);
export const collectionSourceProbes=sqliteTable('collection_source_probes',{
 id:text('id').primaryKey(),sessionId:text('session_id').notNull().references(()=>collectionSessions.id),day:text('day').notNull(),page:integer('page').notNull(),failedAt:text('failed_at').notNull(),attemptOwner:text('attempt_owner').notNull(),status:text('status').notNull(),quotaDay:text('quota_day').notNull(),createdAt:text('created_at').notNull(),completedAt:text('completed_at'),resultJson:text('result_json')
},t=>[uniqueIndex('idx_collection_probe_snapshot').on(t.sessionId,t.day,t.page,t.failedAt),check('collection_probe_status',sql`${t.status} IN ('reserved','complete')`)]);
export const dayCheckpoints=sqliteTable('day_checkpoints',{
 sessionId:text('session_id').notNull(),day:text('day').notNull(),state:text('state').notNull(),nextPage:integer('next_page').notNull(),total:integer('total').notNull(),fetched:integer('fetched').notNull(),matched:integer('matched').notNull(),updatedAt:text('updated_at').notNull()
},t=>[primaryKey({columns:[t.sessionId,t.day]})]);
export const seenSourceKeys=sqliteTable('seen_source_keys',{
 sessionId:text('session_id').notNull(),day:text('day').notNull(),sourceKey:text('source_key').notNull(),mode:text('mode').notNull().default('synthetic'),requestNo:text('request_no').notNull().default(''),sequence:text('sequence').notNull().default(''),changeRank:text('change_rank').notNull().default('0'),stored:integer('stored').notNull().default(1),isActive:integer('is_active').notNull().default(1)
},t=>[primaryKey({columns:[t.sessionId,t.day,t.sourceKey]}),index('idx_seen_logical').on(t.mode,t.isActive,t.requestNo,t.sequence,t.changeRank),index('idx_seen_source').on(t.mode,t.sourceKey,t.isActive)]);

export const datasetEpochs=sqliteTable('dataset_epochs',{mode:text('mode').primaryKey(),revision:integer('revision').notNull()});
export const queryCache=sqliteTable('query_cache',{cacheKey:text('cache_key').primaryKey(),mode:text('mode').notNull(),revision:integer('revision').notNull(),payload:text('payload').notNull(),createdAt:integer('created_at').notNull(),expiresAt:integer('expires_at').notNull()},t=>[index('idx_query_cache_mode_time').on(t.mode,t.createdAt)]);

export const budgetSessions=sqliteTable('budget_sessions',{
 id:text('id').primaryKey(),mode:text('mode').notNull(),service:text('service').notNull(),fiscalYear:integer('fiscal_year').notNull(),snapshotDate:text('snapshot_date').notNull(),historyOnly:integer('history_only').notNull(),state:text('state').notNull(),page:integer('page').notNull(),pageSize:integer('page_size').notNull(),sourceTotal:integer('source_total'),fetched:integer('fetched').notNull(),sawFull:integer('saw_full').notNull(),fixtureTotal:integer('fixture_total').notNull(),maxPayload:integer('max_payload').notNull(),lease:text('lease'),leaseUntil:integer('lease_until').notNull(),error:text('error'),createdAt:text('created_at').notNull(),updatedAt:text('updated_at').notNull()
});
function budgetMoneyIndex(name:string,t:any,x:string){return index(name).on(t.mode,t.service,t.fiscalYear,t.isActive,sql.raw(`CASE WHEN ${x} IS NULL THEN -1 WHEN substr(${x},1,1)='-' THEN 0 ELSE 1 END DESC`),sql.raw(`CASE WHEN substr(${x},1,1)='-' THEN -length(substr(${x},2)) ELSE length(${x}) END DESC`),sql.raw(`CASE WHEN substr(${x},1,1)<>'-' THEN ${x} END DESC`),sql.raw(`CASE WHEN substr(${x},1,1)='-' THEN substr(${x},2) END ASC`),t.sourceKey);}
export const budgetRows=sqliteTable('budget_rows',{
 mode:text('mode').notNull(),service:text('service').notNull(),sourceKey:text('source_key').notNull(),payloadHash:text('payload_hash').notNull(),fiscalYear:integer('fiscal_year').notNull(),snapshotDate:text('snapshot_date').notNull(),category:text('category').notNull(),region:text('region').notNull(),org:text('org').notNull(),dept:text('dept').notNull(),project:text('project').notNull(),rowJson:text('row_json').notNull(),isActive:integer('is_active').notNull(),lastSession:text('last_session').notNull(),updatedAt:text('updated_at').notNull()
},t=>[primaryKey({columns:[t.mode,t.service,t.sourceKey]}),index('idx_budget_scope').on(t.mode,t.service,t.fiscalYear,t.isActive,t.category),index('idx_budget_region').on(t.mode,t.region,t.org),index('idx_budget_order_recent').on(t.mode,t.service,t.fiscalYear,t.isActive,sql.raw('snapshot_date DESC'),t.sourceKey),index('idx_budget_order_org').on(t.mode,t.service,t.fiscalYear,t.isActive,t.org,t.project,t.sourceKey),budgetMoneyIndex('idx_budget_order_amount',t,BUDGET_BASIS),budgetMoneyIndex('idx_budget_order_remaining',t,BUDGET_REMAINING)]);
export const budgetRevisions=sqliteTable('budget_revisions',{
 mode:text('mode').notNull(),service:text('service').notNull(),sourceKey:text('source_key').notNull(),payloadHash:text('payload_hash').notNull(),rowJson:text('row_json').notNull(),firstSeen:text('first_seen').notNull(),firstSnapshot:text('first_snapshot').notNull()
},t=>[primaryKey({columns:[t.mode,t.service,t.sourceKey,t.payloadHash]}),index('idx_budget_history_page').on(t.mode,t.service,sql.raw("json_extract(row_json,'$.fiscal_year')"),sql.raw('first_snapshot DESC'),t.sourceKey,t.payloadHash),index('idx_budget_history_baseline').on(t.mode,t.service,t.sourceKey,sql.raw('first_snapshot DESC'),t.payloadHash)]);
export const budgetSeen=sqliteTable('budget_seen',{sessionId:text('session_id').notNull(),sourceKey:text('source_key').notNull()},t=>[primaryKey({columns:[t.sessionId,t.sourceKey]})]);
export const budgetReceipts=sqliteTable('budget_receipts',{sessionId:text('session_id').notNull(),page:integer('page').notNull(),rows:integer('rows').notNull(),bytes:integer('bytes').notNull(),hash:text('hash').notNull(),committedAt:text('committed_at').notNull()},t=>[primaryKey({columns:[t.sessionId,t.page]})]);
export const sourceUsage=sqliteTable('source_usage',{source:text('source').notNull(),day:text('day').notNull(),calls:integer('calls').notNull()},t=>[primaryKey({columns:[t.source,t.day]})]);
export const budgetControls=sqliteTable('budget_controls',{mode:text('mode').notNull(),lane:text('lane').notNull(),state:text('state').notNull(),updatedAt:text('updated_at').notNull()},t=>[primaryKey({columns:[t.mode,t.lane]})]);
export const budgetEpochs=sqliteTable('budget_epochs',{mode:text('mode').primaryKey(),revision:integer('revision').notNull()});
export const matchingJobs=sqliteTable('matching_jobs',{
 id:text('id').primaryKey(),mode:text('mode').notNull(),state:text('state').notNull(),phase:text('phase').notNull(),engineVersion:text('engine_version').notNull().default('v11'),g2bEpoch:integer('g2b_epoch').notNull(),budgetEpoch:integer('budget_epoch').notNull(),fromDay:text('from_day').notNull(),toDay:text('to_day').notNull(),cursor:text('cursor').notNull(),partial:text('partial'),budgetCount:integer('budget_count').notNull(),requestCount:integer('request_count').notNull(),candidateCount:integer('candidate_count').notNull(),matchCount:integer('match_count').notNull(),excludedCount:integer('excluded_count').notNull(),lease:text('lease'),leaseUntil:integer('lease_until').notNull(),error:text('error'),createdAt:text('created_at').notNull(),updatedAt:text('updated_at').notNull()
});
export const matchingBudgets=sqliteTable('matching_budgets',{jobId:text('job_id').notNull(),sourceKey:text('source_key').notNull(),rowJson:text('row_json').notNull()},t=>[primaryKey({columns:[t.jobId,t.sourceKey]})]);
export const matchingAliases=sqliteTable('matching_aliases',{jobId:text('job_id').notNull(),alias:text('alias').notNull(),sourceKey:text('source_key').notNull()},t=>[primaryKey({columns:[t.jobId,t.alias,t.sourceKey]})]);
export const matchingRequests=sqliteTable('matching_requests',{jobId:text('job_id').notNull(),requestKey:text('request_key').notNull(),rowJson:text('row_json').notNull(),missingAmounts:integer('missing_amounts').notNull(),integrity:text('integrity').notNull()},t=>[primaryKey({columns:[t.jobId,t.requestKey]})]);
export const matchingResults=sqliteTable('matching_results',{jobId:text('job_id').notNull(),requestKey:text('request_key').notNull(),budgetKey:text('budget_key').notNull(),score:integer('score').notNull(),level:text('level').notNull(),rowJson:text('row_json').notNull()},t=>[primaryKey({columns:[t.jobId,t.requestKey]}),index('idx_matching_scores').on(t.jobId,t.score,t.requestKey)]);

export const budgetReplayState=sqliteTable('budget_replay_state',{sessionId:text('session_id').primaryKey(),rootId:text('root_id').notNull(),parentId:text('parent_id'),replayCount:integer('replay_count').notNull(),failurePage:integer('failure_page'),error:text('error'),successorId:text('successor_id'),updatedAt:text('updated_at').notNull()},t=>[uniqueIndex('idx_budget_replay_parent').on(t.parentId)]);

export const budgetHistoryEpochs=sqliteTable('budget_history_epochs',{mode:text('mode').primaryKey(),revision:integer('revision').notNull()});

export const budgetObservations=sqliteTable('budget_observations',{
 mode:text('mode').notNull(),service:text('service').notNull(),sourceKey:text('source_key').notNull(),snapshotDate:text('snapshot_date').notNull(),payloadHash:text('payload_hash').notNull(),fiscalYear:integer('fiscal_year').notNull(),firstSeen:text('first_seen').notNull(),lastSeen:text('last_seen').notNull(),lastSession:text('last_session').notNull()
},t=>[primaryKey({columns:[t.mode,t.service,t.sourceKey,t.snapshotDate,t.payloadHash]}),foreignKey({columns:[t.mode,t.service,t.sourceKey,t.payloadHash],foreignColumns:[budgetRevisions.mode,budgetRevisions.service,budgetRevisions.sourceKey,budgetRevisions.payloadHash]}),check('budget_observations_qwgjk_only',sql`${t.service}='QWGJK'`),index('idx_budget_observation_page').on(t.mode,t.service,t.fiscalYear,sql.raw('snapshot_date DESC'),t.sourceKey,t.payloadHash),index('idx_budget_observation_baseline').on(t.mode,t.service,t.sourceKey,sql.raw('snapshot_date DESC'),t.payloadHash)]);
export const budgetObservationEpochs=sqliteTable('budget_observation_epochs',{mode:text('mode').primaryKey(),revision:integer('revision').notNull()});

export const institutionPatternRuns=sqliteTable('institution_pattern_runs',{
 id:text('id').primaryKey(),requestId:text('request_id').notNull(),aliasVersion:text('alias_version').notNull().default(''),mode:text('mode').notNull(),matchingJobId:text('matching_job_id').notNull(),matchingEngineVersion:text('matching_engine_version').notNull(),patternVersion:text('pattern_version').notNull(),projectionVersion:text('projection_version').notNull(),
 g2bEpoch:integer('g2b_epoch').notNull(),budgetEpoch:integer('budget_epoch').notNull(),fromDay:text('from_day').notNull(),toDay:text('to_day').notNull(),budgetScopeFrom:text('budget_scope_from').notNull(),budgetScopeTo:text('budget_scope_to').notNull(),scopeJson:text('scope_json').notNull(),scopeHash:text('scope_hash').notNull(),
 state:text('state').notNull(),phase:text('phase').notNull(),cursorJson:text('cursor_json').notNull(),partialJson:text('partial_json'),generation:integer('generation').notNull(),minScore:integer('min_score').notNull(),indexedProjects:integer('indexed_projects').notNull(),indexedRequests:integer('indexed_requests').notNull(),excludedProjects:integer('excluded_projects').notNull(),excludedRequests:integer('excluded_requests').notNull(),processedProjects:integer('processed_projects').notNull(),processedRequests:integer('processed_requests').notNull(),completedGroups:integer('completed_groups').notNull(),lease:text('lease'),leaseUntil:integer('lease_until').notNull(),error:text('error'),createdAt:text('created_at').notNull(),updatedAt:text('updated_at').notNull(),completedAt:text('completed_at')
},t=>[foreignKey({columns:[t.matchingJobId],foreignColumns:[matchingJobs.id]}),uniqueIndex('idx_institution_run_request').on(t.requestId),index('idx_institution_run_source').on(t.matchingJobId,t.scopeHash,t.patternVersion,t.projectionVersion),index('idx_institution_run_mode').on(t.mode,sql.raw('created_at DESC'),t.id)]);
export const institutionPatternEvents=sqliteTable('institution_pattern_events',{
 runId:text('run_id').notNull(),kind:text('kind').notNull(),sourceEventKey:text('source_event_key').notNull(),regionKey:text('region_key').notNull(),orgKey:text('org_key').notNull(),category:text('category').notNull(),fiscalYear:integer('fiscal_year').notNull(),budgetKey:text('budget_key').notNull(),kindOrder:integer('kind_order').notNull(),rowJson:text('row_json').notNull(),eligible:integer('eligible').notNull()
},t=>[primaryKey({columns:[t.runId,t.kind,t.sourceEventKey]}),foreignKey({columns:[t.runId],foreignColumns:[institutionPatternRuns.id]}),index('idx_institution_event_page').on(t.runId,t.eligible,t.regionKey,t.orgKey,t.category,t.fiscalYear,t.budgetKey,t.kindOrder,t.sourceEventKey)]);
export const institutionPatternYears=sqliteTable('institution_pattern_years',{
 runId:text('run_id').notNull(),regionKey:text('region_key').notNull(),orgKey:text('org_key').notNull(),category:text('category').notNull(),fiscalYear:integer('fiscal_year').notNull(),rowJson:text('row_json').notNull(),highProjects:integer('high_projects').notNull()
},t=>[primaryKey({columns:[t.runId,t.regionKey,t.orgKey,t.category,t.fiscalYear]}),foreignKey({columns:[t.runId],foreignColumns:[institutionPatternRuns.id]})]);

export const institutionPatternAliases=sqliteTable('institution_pattern_aliases',{
 runId:text('run_id').notNull(),regionKey:text('region_key').notNull(),category:text('category').notNull(),alias:text('alias').notNull(),orgKey:text('org_key').notNull(),fiscalYear:integer('fiscal_year').notNull()
},t=>[primaryKey({columns:[t.runId,t.regionKey,t.category,t.alias,t.orgKey,t.fiscalYear]}),foreignKey({columns:[t.runId,t.regionKey,t.orgKey,t.category,t.fiscalYear],foreignColumns:[institutionPatternYears.runId,institutionPatternYears.regionKey,institutionPatternYears.orgKey,institutionPatternYears.category,institutionPatternYears.fiscalYear]})]);

export const futureRankRuns=sqliteTable('future_rank_runs',{
 id:text('id').primaryKey(),requestId:text('request_id').notNull().unique(),mode:text('mode').notNull(),institutionRunId:text('institution_run_id').notNull(),institutionGeneration:integer('institution_generation').notNull(),institutionScopeHash:text('institution_scope_hash').notNull(),g2bEpoch:integer('g2b_epoch').notNull(),budgetEpoch:integer('budget_epoch').notNull(),rankVersion:text('rank_version').notNull(),optionsJson:text('options_json').notNull(),scopeHash:text('scope_hash').notNull(),sourceLimit:integer('source_limit').notNull(),state:text('state').notNull(),cursorJson:text('cursor_json'),examined:integer('examined').notNull(),ranked:integer('ranked').notNull(),sourceExhausted:integer('source_exhausted').notNull(),lease:text('lease'),leaseUntil:integer('lease_until').notNull(),generation:integer('generation').notNull(),error:text('error'),createdAt:text('created_at').notNull(),updatedAt:text('updated_at').notNull(),completedAt:text('completed_at')
},t=>[foreignKey({columns:[t.institutionRunId],foreignColumns:[institutionPatternRuns.id]}),index('idx_future_rank_mode').on(t.mode,sql.raw('created_at DESC'),t.id),check('future_rank_limit',sql`${t.sourceLimit}>=1 AND ${t.sourceLimit}<=500`)]);
export const futureRankRows=sqliteTable('future_rank_rows',{
 cursorId:text('cursor_id').notNull(),runId:text('run_id').notNull(),sourceKey:text('source_key').notNull(),rankKey:text('rank_key').notNull(),rowJson:text('row_json').notNull()
},t=>[primaryKey({columns:[t.runId,t.sourceKey]}),foreignKey({columns:[t.runId],foreignColumns:[futureRankRuns.id]}),uniqueIndex('idx_future_rank_order').on(t.runId,t.rankKey),uniqueIndex('idx_future_rank_cursor').on(t.runId,t.cursorId)]);

// Independent one-additional-attempt lane. Never reset/delete the original probe.
export const collectionSourceDiagnosticsV2=sqliteTable('collection_source_diagnostics_v2',{
 id:text('id').primaryKey(),priorProbeId:text('prior_probe_id').notNull().references(()=>collectionSourceProbes.id),sessionId:text('session_id').notNull().references(()=>collectionSessions.id),day:text('day').notNull(),page:integer('page').notNull(),failedAt:text('failed_at').notNull(),attemptOwner:text('attempt_owner').notNull(),status:text('status').notNull(),quotaDay:text('quota_day').notNull(),createdAt:text('created_at').notNull(),completedAt:text('completed_at'),resultJson:text('result_json')
},t=>[uniqueIndex('idx_diagnostic_v2_snapshot').on(t.sessionId,t.day,t.page,t.failedAt),uniqueIndex('idx_diagnostic_v2_prior').on(t.priorProbeId),check('diagnostic_v2_status',sql`${t.status} IN ('reserved','complete')`)]);

// Isolated recovery evidence. These tables never feed current delivery queries.
export const g2bRecoveryGenerations=sqliteTable('g2b_recovery_generations',{
 id:text('id').primaryKey(),rootId:text('root_id').notNull().references(()=>collectionSessions.id),diagnosticId:text('diagnostic_id').notNull(),snapshotJson:text('snapshot_json').notNull(),manifestJson:text('manifest_json').notNull(),day:text('day').notNull(),version:text('version').notNull(),windowFrom:text('window_from').notNull(),windowTo:text('window_to').notNull(),state:text('state').notNull(),controlVersion:integer('control_version').notNull().default(0),pass:integer('pass').notNull().default(1),page:integer('page').notNull().default(1),sourceTotal:integer('source_total'),sourceRows:integer('source_rows').notNull().default(0),targetRows:integer('target_rows').notNull().default(0),excludedRows:integer('excluded_rows').notNull().default(0),payloadBytes:integer('payload_bytes').notNull().default(0),attempts:integer('attempts').notNull().default(0),currentRun:text('current_run'),error:text('error'),createdAt:integer('created_at').notNull()
},t=>[uniqueIndex('g2b_recovery_one_root').on(t.rootId),check('g2b_recovery_state',sql`${t.state} IN ('ready','running','paused','cancelled','uncertain','observed_consistent_needs_review')`),check('g2b_recovery_bounds',sql`${t.pass} BETWEEN 1 AND 2 AND ${t.page} BETWEEN 1 AND 21 AND ${t.attempts} BETWEEN 0 AND 42 AND ${t.sourceRows} BETWEEN 0 AND 20000 AND ${t.payloadBytes} BETWEEN 0 AND 67108864 AND ${t.targetRows}+${t.excludedRows}=${t.sourceRows}`)]);
export const g2bRecoveryRuns=sqliteTable('g2b_recovery_runs',{
 id:text('id').primaryKey(),generationId:text('generation_id').notNull().references(()=>g2bRecoveryGenerations.id),owner:text('owner').notNull(),state:text('state').notNull(),startedAt:integer('started_at').notNull(),deadline:integer('deadline').notNull(),attempts:integer('attempts').notNull().default(0),inFlight:integer('in_flight').notNull().default(0),error:text('error')
},t=>[check('g2b_recovery_run_bounds',sql`${t.attempts} BETWEEN 0 AND 8 AND ${t.deadline}=${t.startedAt}+90000 AND ${t.inFlight} IN (0,1)`),check('g2b_recovery_run_state',sql`${t.state} IN ('active','uncertain','finished')`)]);
export const g2bRecoveryAttempts=sqliteTable('g2b_recovery_attempts',{
 id:text('id').primaryKey(),generationId:text('generation_id').notNull().references(()=>g2bRecoveryGenerations.id),runId:text('run_id').notNull().references(()=>g2bRecoveryRuns.id),pass:integer('pass').notNull(),page:integer('page').notNull(),state:text('state').notNull(),quotaDay:text('quota_day').notNull(),createdAt:integer('created_at').notNull(),responseHash:text('response_hash'),orderedHash:text('ordered_hash'),error:text('error'),conflictJson:text('conflict_json')
},t=>[uniqueIndex('g2b_recovery_page_attempt').on(t.generationId,t.pass,t.page),check('g2b_recovery_attempt_bounds',sql`${t.pass} BETWEEN 1 AND 2 AND ${t.page} BETWEEN 1 AND 21`),check('g2b_recovery_attempt_state',sql`${t.state} IN ('reserved','failed','committed')`)]);
export const g2bRecoveryPages=sqliteTable('g2b_recovery_pages',{
 generationId:text('generation_id').notNull().references(()=>g2bRecoveryGenerations.id),pass:integer('pass').notNull(),page:integer('page').notNull(),total:integer('total').notNull(),sourceRows:integer('source_rows').notNull(),targetRows:integer('target_rows').notNull(),excludedRows:integer('excluded_rows').notNull(),orderedHash:text('ordered_hash').notNull(),responseHash:text('response_hash').notNull(),responseBytes:integer('response_bytes').notNull(),committedAt:integer('committed_at').notNull()
},t=>[primaryKey({columns:[t.generationId,t.pass,t.page]}),check('g2b_recovery_page_counts',sql`${t.total} BETWEEN 0 AND 20000 AND ${t.sourceRows} BETWEEN 0 AND 999 AND ${t.targetRows}+${t.excludedRows}=${t.sourceRows}`)]);
export const g2bRecoverySourceRows=sqliteTable('g2b_recovery_source_rows',{
 generationId:text('generation_id').notNull().references(()=>g2bRecoveryGenerations.id),sourceKey:text('source_key').notNull(),page:integer('page').notNull(),ordinal:integer('ordinal').notNull(),payloadHash:text('payload_hash').notNull(),excluded:text('excluded'),sourceDate:text('source_date').notNull(),dateBasis:text('date_basis').notNull()
},t=>[primaryKey({columns:[t.generationId,t.sourceKey]}),uniqueIndex('g2b_recovery_order').on(t.generationId,t.page,t.ordinal)]);
export const g2bRecoveryTargetRows=sqliteTable('g2b_recovery_target_rows',{
 generationId:text('generation_id').notNull().references(()=>g2bRecoveryGenerations.id),sourceKey:text('source_key').notNull(),targetJson:text('target_json').notNull()
},t=>[primaryKey({columns:[t.generationId,t.sourceKey]})]);

// Windows server import receipts are independent of cloud collection sessions.
export const serverSyncPages=sqliteTable('server_sync_pages',{
 id:text('id').primaryKey(),dataset:text('dataset').notNull(),scope:text('scope').notNull(),generation:text('generation').notNull(),pageNo:integer('page_no').notNull(),
 revision:text('revision').notNull(),contentHash:text('content_hash').notNull(),sourceRows:integer('source_rows').notNull(),matchedRows:integer('matched_rows').notNull(),
 sourceSavedCount:integer('source_saved_count').notNull(),sourceTotal:integer('source_total').notNull(),sourceStatus:text('source_status').notNull(),sourceUpdatedAt:text('source_updated_at').notNull(),receivedAt:text('received_at').notNull(),
},t=>[uniqueIndex('idx_server_sync_page').on(t.dataset,t.scope,t.generation,t.pageNo)]);
export const serverSyncKeys=sqliteTable('server_sync_keys',{
 dataset:text('dataset').notNull(),scope:text('scope').notNull(),generation:text('generation').notNull(),sourceKey:text('source_key').notNull(),pageId:text('page_id').notNull(),
},t=>[primaryKey({columns:[t.dataset,t.scope,t.generation,t.sourceKey]})]);
