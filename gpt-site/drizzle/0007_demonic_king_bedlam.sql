CREATE TABLE `budget_controls` (
	`mode` text NOT NULL,
	`lane` text NOT NULL,
	`state` text NOT NULL,
	`updated_at` text NOT NULL,
	PRIMARY KEY(`mode`, `lane`)
);
--> statement-breakpoint
INSERT INTO budget_controls(mode,lane,state,updated_at)
SELECT mode,lane,state,updated_at FROM (
 SELECT mode,CASE WHEN service='QWGJK' AND history_only=1 THEN 'QWGJK_HISTORY' ELSE service END lane,state,error,updated_at,
 ROW_NUMBER() OVER(PARTITION BY mode,CASE WHEN service='QWGJK' AND history_only=1 THEN 'QWGJK_HISTORY' ELSE service END ORDER BY created_at DESC,id DESC) rn
 FROM budget_sessions
) WHERE rn=1 AND state IN ('paused','cancelled') AND COALESCE(error,'')<>'AIDFA_DAY_ADVANCED';
