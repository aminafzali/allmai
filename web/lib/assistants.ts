import {
  BookOpenCheck,
  BrainCircuit,
  Compass,
  DatabaseZap,
  StickyNote,
  type LucideIcon,
} from "lucide-react";

/** Formal palette: navy text/icons + very light tinted card backgrounds
 *  (one soft tint per assistant) + pale orange badges. */
const TILE = "bg-white/80";
const ICON = "text-blue-950";
const BAR = "from-blue-950 to-blue-950";
const BTN = "bg-blue-950 hover:bg-blue-900";

export type Assistant = {
  id: string;
  title: string;
  tagline: string;
  desc: string;
  href: string;
  chatHref: string;
  icon: LucideIcon;
  gradient: string; // top accent bar
  tile: string; // icon tile bg
  iconColor: string;
  button: string; // primary button bg
  card: string; // soft tinted card bg
  points: string[];
  badge?: string;
};

export const ASSISTANTS: Assistant[] = [
  {
    id: "teacher",
    title: "دستیار معلم",
    tagline: "طرح درس حرفه‌ای در چند دقیقه",
    desc: "کتاب و جزوه‌تان را بدهید؛ طرح درس کامل با اهداف، فعالیت، مثال، سؤال، ارزشیابی و ارجاع دقیق صفحه تحویل بگیرید.",
    href: "/teacher/login",
    chatHref: "/teacher/chat",
    icon: BookOpenCheck,
    gradient: BAR,
    card: "border-blue-100/70 bg-blue-50/70",
    tile: TILE,
    iconColor: ICON,
    button: BTN,
    points: ["ارجاع دقیق به صفحه و منبع", "فعالیت گروهی و ارزشیابی", "چت grounded روی منابع شما"],
    badge: "پرتکرارترین",
  },
  {
    id: "student",
    title: "مربی دانش‌آموز",
    tagline: "برنامه هفتگی که دنبالت می‌کند",
    desc: "مربی تحصیلی شخصی: برنامه هفتگی، پیگیری پیشرفت، یادآوری اهداف و پاسخ‌گویی بر اساس حافظه شما.",
    href: "/student/login",
    chatHref: "/student/chat",
    icon: BrainCircuit,
    gradient: BAR,
    card: "border-green-100/70 bg-green-50/70",
    tile: TILE,
    iconColor: ICON,
    button: BTN,
    points: ["پروفایل و حافظه بلندمدت", "برنامه و پیگیری پیشرفت", "کوچینگ قدم‌به‌قدم"],
  },
  {
    id: "notes",
    title: "دستیار یادداشت",
    tagline: "بپرس، فقط از منابع خودت جواب بگیر",
    desc: "فایل، صوت کلاس، ویدئو، اکسل و لینک بفرستید؛ دستیار فقط از روی همان‌ها جواب می‌دهد و سؤال عددی را محاسبه می‌کند.",
    href: "/notes",
    chatHref: "/notes/chat",
    icon: StickyNote,
    gradient: BAR,
    card: "border-amber-100/70 bg-amber-50/70",
    tile: TILE,
    iconColor: ICON,
    button: BTN,
    points: ["متن کامل اسناد قابل مشاهده", "پشتیبانی از صوت و ویدئو", "محاسبات روی اکسل"],
  },
  {
    id: "extraction",
    title: "دستیار استخراج داده",
    tagline: "لید و داده از دل اینترنت",
    desc: "جستجوی وب و مکان در مرورگر شما اجرا می‌شود؛ لیدها در دیتابیس ذخیره و خروجی اکسل می‌گیرید.",
    href: "/extraction",
    chatHref: "/extraction",
    icon: DatabaseZap,
    gradient: BAR,
    card: "border-violet-100/70 bg-violet-50/70",
    tile: TILE,
    iconColor: ICON,
    button: BTN,
    points: ["اجرای جستجو روی دستگاه شما", "جدول لید + دانلود اکسل", "ورود لید به پایگاه دانش"],
    badge: "جدید",
  },
  {
    id: "guidance",
    title: "مشاور هدایت تحصیلی",
    tagline: "از تیپ شخصیتی تا شغل مناسب",
    desc: "آزمون RIASEC بده، تیپت را بشناس و از مرجع ۱۰۰۰+ شغله O_NET مشاغل مناسب خودت را با نقشه راه بگیر.",
    href: "/guidance",
    chatHref: "/guidance",
    icon: Compass,
    gradient: BAR,
    card: "border-rose-100/70 bg-rose-50/70",
    tile: TILE,
    iconColor: ICON,
    button: BTN,
    points: ["آزمون تیپ شخصیتی ۱۸ سؤالی", "تطبیق قطعی با مرجع مشاغل", "نقشه راه ۳۰/۹۰ روزه"],
    badge: "جدید",
  },
];

