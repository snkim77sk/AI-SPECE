CREATE TABLE `collection_source_diagnostics_v2` (
	`id` text PRIMARY KEY NOT NULL,
	`prior_probe_id` text NOT NULL,
	`session_id` text NOT NULL,
	`day` text NOT NULL,
	`page` integer NOT NULL,
	`failed_at` text NOT NULL,
	`attempt_owner` text NOT NULL,
	`status` text NOT NULL,
	`quota_day` text NOT NULL,
	`created_at` text NOT NULL,
	`completed_at` text,
	`result_json` text,
	FOREIGN KEY (`prior_probe_id`) REFERENCES `collection_source_probes`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`session_id`) REFERENCES `collection_sessions`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "diagnostic_v2_status" CHECK("collection_source_diagnostics_v2"."status" IN ('reserved','complete'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `idx_diagnostic_v2_snapshot` ON `collection_source_diagnostics_v2` (`session_id`,`day`,`page`,`failed_at`);--> statement-breakpoint
CREATE UNIQUE INDEX `idx_diagnostic_v2_prior` ON `collection_source_diagnostics_v2` (`prior_probe_id`);