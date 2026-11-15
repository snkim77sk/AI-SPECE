ALTER TABLE `seen_source_keys` ADD `mode` text DEFAULT 'synthetic' NOT NULL;--> statement-breakpoint
ALTER TABLE `seen_source_keys` ADD `request_no` text DEFAULT '' NOT NULL;--> statement-breakpoint
ALTER TABLE `seen_source_keys` ADD `sequence` text DEFAULT '' NOT NULL;--> statement-breakpoint
ALTER TABLE `seen_source_keys` ADD `change_rank` text DEFAULT '0' NOT NULL;--> statement-breakpoint
ALTER TABLE `seen_source_keys` ADD `stored` integer DEFAULT 1 NOT NULL;--> statement-breakpoint
ALTER TABLE `seen_source_keys` ADD `is_active` integer DEFAULT 1 NOT NULL;--> statement-breakpoint
CREATE INDEX `idx_seen_logical` ON `seen_source_keys` (`mode`,`is_active`,`request_no`,`sequence`,`change_rank`);--> statement-breakpoint
CREATE INDEX `idx_seen_source` ON `seen_source_keys` (`mode`,`source_key`,`is_active`);--> statement-breakpoint
UPDATE seen_source_keys SET mode=COALESCE((SELECT mode FROM collection_sessions WHERE id=seen_source_keys.session_id),'synthetic'),request_no=json_extract(source_key,'$[0]'),sequence=json_extract(source_key,'$[2]'),change_rank=COALESCE(NULLIF(ltrim(CAST(json_extract(source_key,'$[1]') AS TEXT),'0'),''),'0');
--> statement-breakpoint
UPDATE seen_source_keys SET stored=CASE WHEN EXISTS(SELECT 1 FROM delivery_rows d WHERE d.mode=seen_source_keys.mode AND d.source_key=seen_source_keys.source_key) THEN 1 ELSE 0 END;
