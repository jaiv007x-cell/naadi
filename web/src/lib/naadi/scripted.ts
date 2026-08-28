import type { CaseId } from "./types";

const RAMESH: [RegExp, string][] = [
  [/namaste|namaskar|ram ram|kasa ahat|kay zhala/i, "Namaskar doctor. Chatit khup jad watatay. Ghaam yetoy."],
  [/kab se|onset|kabse|kiti vel|kitne baje|subah|lunch|gas/i, "Lunch nantar. Sochlo gas. Chatit dabaav, jaise patthar."],
  [/radiation|peeth|haath|arm|jaw|left|jabda/i, "Haan… daab jaw kade jato. Left haath pan."],
  [/sweat|paseena|diaphor|ulati|nausea|ulti|ghaam/i, "Ghaam khup yetoy. Ulti sarkha vatatay."],
  [/pain|dard|scale|kitna|jad/i, "Saat. Bolu shakat nahi. Saans pan kami."],
  [/pci|hospital|lekar|transfer|samajh|consent|family/i, "Je mhantal te kara. Bayko baher aahe. Malyala bhiti watate."],
  [/smoke|beedi|diabetes|sugar|bp|pressure|tablet|gutka/i, "Gutka daily. Sugar chi dawai kadhi kadhi. BP chi amlodipine."],
];

const SUNITA: [RegExp, string][] = [
  [/namaste|namaskar|kaise|kaisi/i, "Pranam doctor saheb. Sir fata ja raha hai. Pair bhi sooj gaye hain."],
  [/kab se|headache|sir|dard|spark|chamak|aankh/i, "Do din se sir dard. Raat ko aankh ke aage jugnu. Ulti bhi hui subah."],
  [/kick|bachcha|baby|pet|week|mahina/i, "Saatven mahine chal raha hai. Bachcha kam hil raha hai kal se."],
  [/bp|pressure|swell|sooj|peeshab|urine/i, "Pehle bhi pressure tha lekin dawai chhod di. Pair itna sooj gaye ki chappal nahi pehenti."],
  [/refer|lekar|hospital|samajh|saas|family/i, "Jo keho, chali jaungi. Saas ko phone kar dena. Main darti hoon bacche ke liye."],
];

const ARJUN: [RegExp, string][] = [
  [/namaste|namaskar|namaskaram|ela unnaru|hello/i, "Namaskaram doctor garu. Vaadu night antha kosta unnadu. Chala bayapaduthunnadu."],
  [/when|ela|eppudu|night|cough|dust|school/i, "School function lo dust ekkuva. Evening nunchi tight chest. Inhaler okasari ichanu, use cheyaledu correct ga."],
  [/fever|khaana|eat|allergy|asthma|wheeze/i, "Fever ledhu. Chinna nunchi wheeze untundi winter lo. Nebs twice last year."],
  [/spacer|teach|home|plan/i, "Cheppandi ela vadalo. Nenu chestanu. Please vaadini baaga choodandi."],
];

const MEERA: [RegExp, string][] = [
  [/namaste|vanakkam|hello|doctor/i, "Vanakkam doctor. Kuzhandhai saapadala. Udal soodu maathiri irukku."],
  [/sleepy|sleep|feeding|feed|milk|saapadu/i, "Last two feeds refused. Maybe he is just sleepy? Mother-in-law said wait till morning."],
  [/fever|warm|soodu|temp|breath|moochu/i, "Breathing fast since morning. Body feels warm. I am scared."],
  [/water|bag|pani|membrane|delivery|labour|labour/i, "Water broke yesterday morning — almost a full day before he came. I got one injection at nursing home."],
  [/prom|antibiotic|ampicillin|gbs|screen/i, "They gave one antibiotic during delivery. I do not know if that is enough. No test for that germ."],
  [/hospital|nicu|refer|samajh|explain|panic/i, "Tell me honestly doctor. I will do anything. Do not scare me but do not hide."],
];

const PPH: [RegExp, string][] = [
  [/namaste|namaskar|kaise|pranam/i, "Pranam doctor saheb. Khoon ab bhi aa raha hai. Chakkar aa rahe hain."],
  [/delivery|baccha|dai|placenta|ghar/i, "Ghar par dai ne delivery karayi. Placenta nikla bola par kisi ne dekha nahi."],
  [/bleed|khoon|clot|massage|pet/i, "Pet daba rahi hoon par khoon ruk nahi raha. Pair kamzor ho gaye."],
  [/refer|hospital|blood|chale|husband/i, "Patidev motorcycle se laye — 40 minute laga. Khoon milega yahan?"],
];

const DENGUE: [RegExp, string][] = [
  [/namaste|namaskar|namaskaram|hello|doctor/i, "Namaskaram doctor. Fever went down but he is very dull. Tummy pain since morning."],
  [/fever|temp|afebrile|down|curve|day/i, "Day four today. Fever broke last night — I thought he was getting better. Now he won't eat."],
  [/pain|tummy|abdomen|vomit|platelet|dengue/i, "One vomit this morning. I read online about dengue warning signs. Is this that phase?"],
  [/weight|fluid|ml|protocol|admit|discharge/i, "Tell me what to do. I can stay in hospital. Please don't send us home if it's risky."],
];

const PRIYA: [RegExp, string][] = [
  [/namaste|namaskar|hello|doctor|ente/i, "Doctor, pambu kachichu — right ankle. Blood in spit and urine."],
  [/when|time|hour|ozhichil|healer|delay/i, "Two hours since bite. First we went to ozhichil practitioner — then here."],
  [/swell|pain|bite|ankle|blood|clot/i, "Leg swelling fast. Gums bleeding when I spit. Urine dark like cola."],
  [/asv|vial|fasciotomy|hospital|refer/i, "Do whatever is needed. I am scared about the leg but I trust you."],
];

const ANA: [RegExp, string][] = [
  [/namaste|namaskar|kasa|kay zhala/i, "Doctor… injection nantar. Khaj yetoy. Gala atkatoy."],
  [/allergy|pehle|pehela|drug|dawai|ceftriaxone/i, "Pahili dafa hi injection. Allergy mahit nahi. Fever sathi dilay."],
  [/itch|khaj|rash|saans|breath|voice|aawaaz/i, "Khaj sarva angala. Aawaaz khasatoy. Saans takatay."],
  [/family|samajh|hospital/i, "Saheli sobat aahe. Kahi hi kara. Bhiti watate."],
];

const FALLBACK: Record<string, string> = {
  "ramesh-stemi": "Kuch karo na doctor… dard badh raha hai.",
  "sunita-preeclampsia": "Sir aur bhi zyada dard kar raha hai. Aankh kalila si hai.",
  "sunita-pph": "Khoon ruk nahi raha doctor saheb… chakkar aa rahe hain.",
  "aarav-sepsis": "Meera: Doctor, kuzhandhai innum saapadala. Nen bayapaduren.",
  "arjun-asthma": "Amma: Vaadu inka kastapaduthunnadu doctor garu. Breathing fast ga undi.",
  "arjun-dengue": "Amma: Fever went down but he is very dull doctor. Tummy pain since morning.",
  "priya-snakebite": "Doctor, pambu kachichu — blood mutathilum urineyilum.",
  "opd-anaphylaxis": "Saans takatay doctor… please.",
};

export function scriptedReply(caseId: CaseId, text: string, opening?: boolean): string {
  if (opening) {
    if (caseId === "ramesh-stemi") return "Doctor, chatit jad watatay, ghaam yetoy.";
    if (caseId === "sunita-preeclampsia")
      return "Doctor saheb, sir mein bahut dard hai. Aankh ke aage jugnu jaise chamak rahe hain.";
    if (caseId === "sunita-pph")
      return "Doctor saheb, khoon ab bhi aa raha hai. Chakkar aa rahe hain.";
    if (caseId === "aarav-sepsis")
      return "Doctor, kuzhandhai saapadala, udal soodu maathiri irukku. Maybe he is just sleepy?";
    if (caseId === "opd-anaphylaxis") return "Doctor… injection nantar khaj, saans takatay. Aawaaz badaltoy.";
    if (caseId === "arjun-dengue")
      return "Amma: Doctor, fever went down but he is very dull today. Tummy pain since morning.";
    if (caseId === "priya-snakebite")
      return "Doctor, pambu kachichu — ozhichilil poyi. Ippo blood mutathilum urineyilum.";
    return "Amma: Doctor garu, vaadu night antha kosta unnadu. Breathing kastam ga undi.";
  }
  const table =
    caseId === "aarav-sepsis"
      ? MEERA
      : caseId === "sunita-pph"
        ? PPH
        : caseId === "sunita-preeclampsia"
          ? SUNITA
          : caseId === "arjun-dengue"
            ? DENGUE
            : caseId === "priya-snakebite"
              ? PRIYA
              : caseId === "arjun-asthma"
                ? ARJUN
                : caseId === "opd-anaphylaxis"
                  ? ANA
                  : RAMESH;
  for (const [re, line] of table) {
    if (re.test(text)) return line;
  }
  return FALLBACK[caseId] ?? "Doctor… abhi theek nahi lag raha.";
}

export function inferOrdersFromUtterance(caseId: CaseId, text: string): string[] {
  const t = text.toLowerCase();
  const hits: string[] = [];
  if (/namaste|namaskar|namaskaram|ram ram|kasa ahat|ela unnaru|pranam|vanakkam/.test(t))
    hits.push(caseId === "aarav-sepsis" ? "greet_meera" : "greet_native");
  if (caseId === "aarav-sepsis") {
    if (/feed|prom|water|bag|delivery|antibiotic|sleep/.test(t)) hits.push("proxy_history");
  } else if (caseId === "arjun-dengue") {
    if (/fever|day|curve|vomit|pain|dengue|platelet|letharg/.test(t)) hits.push("caregiver_history");
  } else if (caseId === "priya-snakebite") {
    if (/bite|hour|ozhichil|healer|swell|blood|time/.test(t)) hits.push("history");
  } else if (caseId === "arjun-asthma") {
    if (/when|eppudu|night|dust|inhaler|allergy|asthma|fever/.test(t)) hits.push("caregiver_history");
  } else if (caseId === "opd-anaphylaxis") {
    if (/allergy|ceftriaxone|injection|pehle/.test(t)) hits.push("history");
  } else if (/kab se|onset|dard|sir|pain|sweat|paseena|kick|bachcha|radiation/.test(t)) {
    hits.push("history");
  }
  if (/pci|refer|lekar|hospital|samajh|consent|explain|family|plan|spacer/.test(t)) hits.push("consent");
  return hits;
}
