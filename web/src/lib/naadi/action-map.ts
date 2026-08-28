import type { CaseId } from "./types";

/** Frontend order tray id → Pratibimb clinical action id */
const ORDER_ACTION: Record<string, Record<string, string>> = {
  "ramesh-stemi": {
    greet_native: "greet_patient_in_native_language",
    history: "take_focused_history",
    vitals: "measure_vitals",
    ecg: "order_ecg_within_10min",
    troponin: "order_troponin",
    aspirin: "give_aspirin_325_chewed",
    statin: "give_atorvastatin_80",
    pci_transfer: "arrange_pci_transfer",
    consent: "obtain_consent_transfer",
    oxygen: "give_oxygen",
    fluids: "give_iv_fluid_ns",
    morphine: "give_morphine",
  },
  "opd-anaphylaxis": {
    greet_native: "greet_patient_in_native_language",
    history: "take_focused_history",
    vitals: "measure_vitals",
    oxygen: "give_oxygen",
    iv_fluids: "give_iv_fluid_ns",
  },
};

export function orderToPratibimbAction(caseId: CaseId, orderId: string): string | null {
  return ORDER_ACTION[caseId]?.[orderId] ?? null;
}

export function caseSupportsBackend(caseId: CaseId): boolean {
  return caseId in ORDER_ACTION;
}
