import { clamp } from "@/lib/utils";
import type { CaseBlueprint, EndReason, Session, Vitals } from "./types";

function roundVitals(v: Vitals): Vitals {
  return {
    hr: Math.round(clamp(v.hr, 30, 220)),
    sbp: Math.round(clamp(v.sbp, 40, 240)),
    dbp: Math.round(clamp(v.dbp, 20, 140)),
    spo2: Math.round(clamp(v.spo2, 50, 100)),
    rr: Math.round(clamp(v.rr, 6, 70)),
    pain: Math.round(clamp(v.pain, 0, 10)),
    tempC: Math.round(clamp(v.tempC, 34, 41) * 10) / 10,
  };
}

export function applyOrder(session: Session, bp: CaseBlueprint, orderId: string): Session {
  const flags = { ...session.flags };
  const v = { ...session.vitals };
  const findingsSeen = [...session.findingsSeen];
  if (bp.findings[orderId] && !findingsSeen.includes(orderId)) findingsSeen.push(orderId);

  if (bp.id === "ramesh-stemi") {
    if (orderId === "aspirin") flags.aspirin = true;
    if (orderId === "ecg") flags.ecg = true;
    if (orderId === "v4r") flags.v4r = true;
    if (orderId === "fluids") {
      v.sbp += 10;
      v.dbp += 4;
      flags.fluids = true;
    }
    if (orderId === "oxygen") v.spo2 = Math.max(v.spo2, 96);
    if (orderId === "morphine") {
      v.pain -= 3;
      v.sbp -= 4;
      v.rr -= 2;
    }
    if (orderId === "nitro") {
      flags.nitro_rv = true;
      v.sbp -= 30;
      v.dbp -= 12;
      v.hr += 12;
    }
    if (orderId === "iv_epi") {
      flags.iv_epi = true;
      v.hr += 40;
      v.sbp += 30;
    }
    if (orderId === "pci_transfer") flags.transferred = true;
    if (orderId === "thrombolysis") flags.lysed = true;
  }

  if (bp.id === "sunita-preeclampsia") {
    if (orderId === "vitals") flags.bp_repeat = true;
    if (orderId === "urine_protein") flags.protein = true;
    if (orderId === "magnesium") {
      flags.mgso4 = true;
      v.sbp -= 6;
    }
    if (orderId === "labetalol") {
      v.sbp -= 18;
      v.dbp -= 10;
      flags.antihtn = true;
    }
    if (orderId === "left_lateral") v.sbp -= 4;
    if (orderId === "referral_higher") flags.referred = true;
    if (orderId === "discharge_home") flags.sent_home = true;
  }

  if (bp.id === "sunita-pph") {
    if (orderId === "vitals") flags.shock_assessed = true;
    if (orderId === "uterine_massage") {
      flags.massage = true;
      v.hr -= 6;
    }
    if (orderId === "oxytocin" || orderId === "misoprostol") {
      flags.uterotonic = true;
      v.hr -= 10;
      v.sbp += 8;
    }
    if (orderId === "fluid_bolus") {
      v.sbp += 12;
      flags.fluids = true;
    }
    if (orderId === "iv_access") flags.iv = true;
    if (orderId === "referral_higher") flags.referred = true;
    if (orderId === "discharge_home") flags.sent_home = true;
  }

  if (bp.id === "arjun-asthma") {
    if (orderId === "vitals") flags.sats = true;
    if (orderId === "oxygen") v.spo2 = Math.max(v.spo2, 94);
    if (orderId === "nebulized_salbutamol") {
      flags.neb = true;
      v.spo2 += 4;
      v.rr -= 8;
      v.hr -= 8;
    }
    if (orderId === "steroid") flags.steroid = true;
    if (orderId === "aspirin") flags.child_asa = true;
    if (orderId === "sedation") {
      flags.sedated = true;
      v.rr -= 10;
      v.spo2 -= 8;
    }
    if (orderId === "referral_higher") flags.admitted = true;
  }

  if (bp.id === "aarav-sepsis") {
    if (orderId === "warmer") flags.warmer = true;
    if (orderId === "neonatal_vitals") flags.danger_cluster = true;
    if (orderId === "proxy_history") flags.proxy_history = true;
    if (orderId === "glucose") flags.glucose_checked = true;
    if (orderId === "amp_gent") {
      flags.abx = true;
      v.hr -= 12;
      v.rr -= 6;
    }
    if (orderId === "fluid_10") {
      v.sbp += 8;
      flags.fluid_10 = true;
    }
    if (orderId === "fluid_20") {
      flags.fluid_20_trap = true;
      v.spo2 -= 6;
      v.rr += 8;
    }
    if (orderId === "antipyretic_only") flags.antipyretic_only = true;
    if (orderId === "lp" && (v.spo2 < 92 || !flags.abx)) {
      flags.lp_decomp = true;
      v.spo2 -= 10;
      v.hr += 20;
    }
    if (orderId === "refer_nicu") flags.referred = true;
  }

  if (bp.id === "opd-anaphylaxis") {
    if (orderId === "adrenaline_im") {
      flags.im_adrenaline = true;
      v.sbp += 18;
      v.hr -= 10;
      v.spo2 += 4;
    }
    if (orderId === "adrenaline_iv") {
      flags.iv_adrenaline = true;
      v.hr += 40;
      v.sbp += 35;
    }
    if (orderId === "oxygen") v.spo2 = Math.max(v.spo2, 95);
    if (orderId === "iv_fluids") {
      v.sbp += 10;
      flags.fluids = true;
    }
    if (orderId === "ceftriaxone") {
      flags.allergen_repeated = true;
      v.sbp -= 20;
      v.spo2 -= 8;
    }
    if (orderId === "observe_biphasic") flags.observed = true;
  }

  if (bp.id === "arjun-dengue") {
    if (orderId === "vitals") flags.critical_seen = true;
    if (orderId === "weight") flags.weight = true;
    if (orderId === "iv_fluids") {
      flags.fluids = true;
      v.hr -= 8;
      v.sbp += 6;
    }
    if (orderId === "adult_bolus") {
      flags.adult_bolus = true;
      v.sbp -= 15;
    }
    if (orderId === "admit") flags.admitted = true;
    if (orderId === "discharge") flags.sent_home = true;
    if (orderId === "counsel") flags.counseled = true;
  }

  if (bp.id === "priya-snakebite") {
    if (orderId === "wbct20") flags.wbct = true;
    if (orderId === "asv_10") {
      flags.asv = true;
      v.sbp += 8;
    }
    if (orderId === "asv_weight") flags.asv_weight_trap = true;
    if (orderId === "fasciotomy") {
      flags.fasciotomy = true;
      v.spo2 -= 5;
    }
    if (orderId === "creatinine") flags.aki_watch = true;
    if (orderId === "history") flags.healer_timeline = true;
    if (orderId === "referral_higher") flags.referred = true;
  }

  const known = [
    "ramesh-stemi",
    "sunita-preeclampsia",
    "sunita-pph",
    "arjun-asthma",
    "aarav-sepsis",
    "opd-anaphylaxis",
    "arjun-dengue",
    "priya-snakebite",
  ];
  if (!known.includes(bp.id)) {
    if (orderId === "pci_transfer" || orderId.includes("pci")) flags.transferred = true;
    if (orderId === "referral_higher" || orderId.includes("refer")) flags.referred = true;
    if (orderId === "discharge_home") flags.sent_home = true;
    if (orderId === "nitro") {
      flags.nitro_rv = true;
      v.sbp -= 18;
    }
  }

  return { ...session, vitals: roundVitals(v), flags, findingsSeen };
}

export function tickPhysio(session: Session, bp: CaseBlueprint, dtMin: number): Session {
  if (session.status !== "running") return session;
  const v = { ...session.vitals };
  const flags = { ...session.flags };
  const t = Math.min(session.tMin + dtMin, bp.durationMin + 3);

  if (bp.id === "ramesh-stemi") {
    if (!flags.aspirin && t > 5) v.hr += 0.55 * dtMin;
    if (!flags.nitro_rv && t > 10) {
      v.sbp = Math.max(85, v.sbp - 0.8 * dtMin);
    }
    if (!flags.ecg && t > 7) {
      v.hr -= 0.9 * dtMin;
      flags.brady_risk = true;
    }
    if (flags.nitro_rv) {
      v.sbp -= 2.2 * dtMin;
    }
    if (v.sbp < 70 || t > bp.durationMin + 1) flags.coding = true;
  }

  if (bp.id === "sunita-preeclampsia") {
    if (!flags.mgso4) {
      v.sbp += 0.4 * dtMin;
      v.hr += 0.2 * dtMin;
    }
    if (flags.sent_home) flags.coding = true;
    if (t > bp.durationMin && !flags.referred) flags.coding = true;
  }

  if (bp.id === "sunita-pph") {
    if (!flags.uterotonic) {
      v.hr += 0.5 * dtMin;
      v.sbp -= 0.9 * dtMin;
    }
    if (flags.sent_home) flags.coding = true;
    if (t > bp.durationMin && !flags.referred) flags.coding = true;
  }

  if (bp.id === "aarav-sepsis") {
    if (!flags.abx && !flags.antipyretic_only) {
      if (t > 8) v.tempC -= 0.08 * dtMin;
      if (t > 10) {
        v.rr += 0.4 * dtMin;
        flags.apnea_risk = true;
      }
      if (t > 12 && v.tempC < 37) flags.hypothermia_misread = true;
      v.hr += 0.3 * dtMin;
      if (t > 15) v.hr -= 1.2 * dtMin;
    }
    if (flags.antipyretic_only && !flags.abx) {
      v.tempC -= 0.12 * dtMin;
      v.rr += 0.5 * dtMin;
      v.hr += 0.4 * dtMin;
    }
    if (flags.fluid_20_trap) v.spo2 -= 0.35 * dtMin;
    if (!flags.warmer && t > 6) v.tempC -= 0.06 * dtMin;
    if (v.spo2 < 85 || v.hr < 80 || t > bp.durationMin + 1) flags.coding = true;
  }

  if (bp.id === "arjun-asthma") {
    if (!flags.neb) {
      v.spo2 -= 0.25 * dtMin;
      v.rr += 0.3 * dtMin;
    }
    if (flags.sedated) v.spo2 -= 0.4 * dtMin;
    if (v.spo2 < 80 || t > bp.durationMin) flags.coding = true;
  }

  if (bp.id === "opd-anaphylaxis") {
    if (!flags.im_adrenaline) {
      v.sbp -= 1.6 * dtMin;
      v.spo2 -= 0.8 * dtMin;
      v.hr += 0.6 * dtMin;
    }
    if (flags.allergen_repeated) v.sbp -= 2.5 * dtMin;
    if (v.sbp < 60 || v.spo2 < 78 || t > bp.durationMin + 1) flags.coding = true;
  }

  if (bp.id === "arjun-dengue") {
    if (!flags.fluids) {
      v.hr += 0.35 * dtMin;
      v.sbp -= 0.5 * dtMin;
    }
    if (flags.sent_home) flags.coding = true;
    if (t > bp.durationMin && !flags.admitted) flags.coding = true;
  }

  if (bp.id === "priya-snakebite") {
    if (!flags.asv) {
      v.sbp -= 0.7 * dtMin;
      flags.bleeding = true;
    }
    if (flags.fasciotomy) v.spo2 -= 0.3 * dtMin;
    if (v.sbp < 70 || t > bp.durationMin) flags.coding = true;
  }

  const knownTick = [
    "ramesh-stemi",
    "sunita-preeclampsia",
    "sunita-pph",
    "aarav-sepsis",
    "arjun-asthma",
    "opd-anaphylaxis",
    "arjun-dengue",
    "priya-snakebite",
  ];
  if (!knownTick.includes(bp.id)) {
    if (t > bp.durationMin) flags.coding = true;
  }

  return { ...session, tMin: Math.round(t * 10) / 10, vitals: roundVitals(v), flags };
}

export function detectEnd(session: Session, bp: CaseBlueprint): EndReason | null {
  if (session.flags.transferred) return "transferred";
  if (session.flags.referred || session.flags.admitted) return "referral";
  if (bp.id === "arjun-asthma" && session.flags.neb && session.flags.steroid && session.vitals.spo2 >= 94) {
    if (session.tMin >= 16) return "stabilized";
  }
  if (
    bp.id === "opd-anaphylaxis" &&
    session.flags.im_adrenaline &&
    session.flags.observed &&
    session.vitals.sbp >= 90 &&
    session.tMin >= 8
  ) {
    return "stabilized";
  }
  if (bp.id === "aarav-sepsis" && session.flags.abx && session.flags.warmer && session.vitals.spo2 >= 92) {
    if (session.tMin >= 14) return "stabilized";
  }
  if (
    bp.id === "arjun-dengue" &&
    session.flags.fluids &&
    session.flags.admitted &&
    session.tMin >= 12
  ) {
    return "stabilized";
  }
  if (bp.id === "priya-snakebite" && session.flags.asv && session.flags.referred && session.tMin >= 10) {
    return "referral";
  }
  if (session.flags.coding || session.vitals.sbp < 60 || session.vitals.spo2 < 78) return "coded";
  if (session.tMin >= bp.durationMin) return "time";
  if (session.flags.sent_home) return "abandoned";
  return null;
}
