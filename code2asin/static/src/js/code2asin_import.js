/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Dialog } from "@web/core/dialog/dialog";
import { session } from "@web/session";
import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillDestroy, onMounted, useState, useRef } from "@odoo/owl";

class ImportDialog extends Component {
    static template = "code2asin.ImportDialog";
    static props = {
        filename: { type: String, optional: true },
        close: { type: Function },
    };

    setup() {
        this.state = useState({
            progressValue: 0,
            progressStage: _t('Preparing'),
            importStarted: false,
            info: [],
        });
        
        this.filename = this.props.filename || _t('CSV File');
        this.channelName = `code2asin_import_${session.user_id}`;
        this.busService = useService("bus_service");
        this.rpc = useService("rpc");
        this.notification = useService("notification");
        
        this.importInfoRef = useRef("importInfo");
        
        onMounted(() => {
            // Subscribe to the bus channel for progress updates
            this.busService.addChannel(this.channelName);
            this.busService.addEventListener("notification", this._onBusNotification.bind(this));
        });
        
        onWillDestroy(() => {
            // Unsubscribe from the bus channel
            this.busService.removeChannel(this.channelName);
        });
    }
    
    /**
     * @private
     * @param {Number} value - Progress value (0-100)
     * @param {String} stage - Current stage description
     */
    _updateProgress(value, stage) {
        this.state.progressValue = value;
        this.state.progressStage = stage;
    }
    
    /**
     * @private
     * @param {Array} notifications - Bus notifications
     */
    _onBusNotification({ detail: notifications }) {
        for (const { payload, type } of notifications) {
            if (type === this.channelName && payload.type === 'code2asin_progress') {
                this._updateProgress(payload.progress, payload.stage);
                
                if (payload.info) {
                    this.state.info.push(payload.info);
                    // Auto-scroll to bottom
                    if (this.importInfoRef.el) {
                        browser.setTimeout(() => {
                            this.importInfoRef.el.scrollTop = this.importInfoRef.el.scrollHeight;
                        }, 0);
                    }
                }
                
                // If import is complete, close the dialog after a delay
                if (payload.progress === 100 && payload.stage === _t('Complete')) {
                    browser.setTimeout(() => {
                        this.props.close();
                    }, 2000);
                }
            }
        }
    }
    
    /**
     * @private
     */
    onStartImport() {
        if (this.state.importStarted) {
            return;
        }
        
        this.state.importStarted = true;
        this._updateProgress(5, _t('Starting import...'));
        
        // Get the replace images option value
        const replaceImages = this.el.querySelector('.o_code2asin_replace_images').checked;
        
        // Call the server to start the import process
        this.rpc("/web/dataset/call_button", {
            model: 'res.config.settings',
            method: 'action_start_import_process',
            args: [],
            kwargs: {
                'replace_images': replaceImages
            },
        }).then((result) => {
            // The import process is running in the background
            // Progress updates will come through the bus
        }).catch((error) => {
            console.error('Import failed:', error);
            this.notification.add(_t('Import failed. Please check the server logs.'), {
                type: 'danger',
            });
        });
    }
    
    /**
     * @private
     */
    onCancelImport() {
        this.props.close();
    }
}

// Register the client action
registry.category("actions").add("code2asin_import_dialog", function (env) {
    return {
        type: "ir.actions.client",
        tag: "code2asin_import_dialog",
        name: _t("Import Products from Code2ASIN"),
        params: {},
        target: "new",
        exec: function () {
            env.services.dialog.add(ImportDialog, {
                filename: env.action.params?.filename || _t('CSV File'),
            });
        },
    };
});

export default ImportDialog;
