CREATE TABLE `budget_history_epochs` (
	`mode` text PRIMARY KEY NOT NULL,
	`revision` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_budget_history_page` ON `budget_revisions` (`mode`,`service`,json_extract(row_json,'$.fiscal_year'),first_snapshot DESC,`source_key`,`payload_hash`);--> statement-breakpoint
CREATE INDEX `idx_budget_history_baseline` ON `budget_revisions` (`mode`,`service`,`source_key`,first_snapshot DESC,`payload_hash`);
--> statement-breakpoint
CREATE TRIGGER budget_history_epoch_insert AFTER INSERT ON budget_revisions BEGIN
  INSERT INTO budget_history_epochs(mode,revision) VALUES(NEW.mode,1) ON CONFLICT(mode) DO UPDATE SET revision=revision+1;
END;
--> statement-breakpoint
CREATE TRIGGER budget_history_epoch_update AFTER UPDATE ON budget_revisions BEGIN
  INSERT INTO budget_history_epochs(mode,revision) VALUES(OLD.mode,1) ON CONFLICT(mode) DO UPDATE SET revision=revision+1;
  INSERT INTO budget_history_epochs(mode,revision) SELECT NEW.mode,1 WHERE NEW.mode<>OLD.mode ON CONFLICT(mode) DO UPDATE SET revision=revision+1;
END;
--> statement-breakpoint
CREATE TRIGGER budget_history_epoch_delete AFTER DELETE ON budget_revisions BEGIN
  INSERT INTO budget_history_epochs(mode,revision) VALUES(OLD.mode,1) ON CONFLICT(mode) DO UPDATE SET revision=revision+1;
END;
