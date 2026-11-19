from django import forms
from django.core.validators import FileExtensionValidator
from django.forms import inlineformset_factory

from apps.organizations.models import Organization
from apps.speakers.models import SpeakerLink, SpeakerProfile

MAX_HEADSHOT_MB = 5


class ProfileForm(forms.ModelForm):
    class Meta:
        model = SpeakerProfile
        fields = [
            "first_name", "last_name", "pronouns", "job_title", "affiliation", "bio", "headshot",
            "email", "phone", "city", "region", "country",
        ]  # fmt: skip
        labels = {
            "affiliation": "Organization / affiliation",
            "email": "Contact email",
            "region": "State / region",
            "country": "Country (2-letter code)",
        }
        help_texts = {
            "bio": "A short third-person bio, as it should appear in the schedule.",
            "email": "Shared only with organizations you allow (see Sharing).",
        }
        widgets = {"bio": forms.Textarea(attrs={"rows": 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name"):
            self.fields[name].required = True
        for name, field in self.fields.items():
            if name != "headshot":
                field.widget.attrs.setdefault("class", "input")

    def clean_headshot(self):
        image = self.cleaned_data.get("headshot")
        if image and hasattr(image, "size") and image.size > MAX_HEADSHOT_MB * 1024 * 1024:
            raise forms.ValidationError(f"Please upload an image under {MAX_HEADSHOT_MB} MB.")
        return image

    def clean_country(self):
        return (self.cleaned_data.get("country") or "").upper()


LinkFormSet = inlineformset_factory(
    SpeakerProfile,
    SpeakerLink,
    fields=["kind", "url", "label"],
    extra=2,
    can_delete=True,
    widgets={
        "kind": forms.Select(attrs={"class": "input"}),
        "url": forms.URLInput(attrs={"class": "input", "placeholder": "https://"}),
        "label": forms.TextInput(attrs={"class": "input", "placeholder": "Label (optional)"}),
    },
)


class GrantForm(forms.Form):
    organization = forms.ModelChoiceField(queryset=Organization.objects.none(), empty_label="Choose an organization")

    def __init__(self, *args, exclude_ids=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organization"].queryset = Organization.objects.exclude(pk__in=exclude_ids).order_by("name")
        self.fields["organization"].widget.attrs["class"] = "input"


class SessionEditForm(forms.Form):
    title = forms.CharField(max_length=500, widget=forms.TextInput(attrs={"class": "input"}))
    summary = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"class": "input", "rows": 2}), help_text="One or two sentences."
    )
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "input", "rows": 8}))


class WithdrawForm(forms.Form):
    reason = forms.CharField(
        required=False,
        label="Anything the organizers should know? (optional)",
        widget=forms.Textarea(attrs={"class": "input", "rows": 3}),
    )


class EventInfoForm(forms.Form):
    """The event's speaker-info questions (pitches.FormQuestion, form=speaker_info) plus recording consent."""

    recording_consent = forms.TypedChoiceField(
        label="May this event record your sessions?",
        choices=[("", "Not answered yet"), ("yes", "Yes"), ("no", "No")],
        coerce=lambda v: {"yes": True, "no": False}.get(v),
        empty_value=None,
        required=False,
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, questions=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.questions = list(questions)
        for q in self.questions:
            self.fields[f"q_{q.slug}"] = question_field(q)

    def answers(self) -> dict:
        return {q.slug: self.cleaned_data.get(f"q_{q.slug}") for q in self.questions}


def question_field(q) -> forms.Field:
    """A form field for one custom question (pitches.FormQuestion)."""
    from apps.pitches.models import FormQuestion

    kind = FormQuestion.Kind
    common = {"label": q.prompt, "help_text": q.help_text, "required": q.required}
    if q.kind == kind.LONG_TEXT:
        return forms.CharField(widget=forms.Textarea(attrs={"rows": 4, "class": "input"}), **common)
    if q.kind == kind.EMAIL:
        return forms.EmailField(widget=forms.EmailInput(attrs={"class": "input"}), **common)
    if q.kind == kind.URL:
        return forms.URLField(widget=forms.URLInput(attrs={"class": "input"}), **common)
    if q.kind == kind.INTEGER:
        return forms.IntegerField(widget=forms.NumberInput(attrs={"class": "input"}), **common)
    if q.kind == kind.BOOLEAN:
        return forms.BooleanField(**{**common, "required": False})
    if q.kind == kind.SINGLE_CHOICE:
        return forms.ChoiceField(choices=[(c, c) for c in q.choices], widget=forms.RadioSelect, **common)
    if q.kind == kind.MULTI_CHOICE:
        return forms.MultipleChoiceField(
            choices=[(c, c) for c in q.choices], widget=forms.CheckboxSelectMultiple, **common
        )
    return forms.CharField(widget=forms.TextInput(attrs={"class": "input"}), **common)


class PitchForm(forms.Form):
    """A pitch plus each chosen event's custom questions (fields `e<event id>_<slug>`)."""

    title = forms.CharField(max_length=500, widget=forms.TextInput(attrs={"class": "input"}))
    description = forms.CharField(
        widget=forms.Textarea(attrs={"class": "input", "rows": 8}),
        help_text="What attendees will learn, and why now.",
    )
    format = forms.CharField(
        max_length=100,
        required=False,
        label="Suggested format",
        widget=forms.TextInput(attrs={"class": "input", "list": "formats"}),
    )
    skill_level = forms.CharField(
        max_length=100, required=False, widget=forms.TextInput(attrs={"class": "input", "list": "levels"})
    )
    speaker_suggestions = forms.CharField(
        required=False,
        label="Other speakers you'd suggest (not yet asked)",
        widget=forms.Textarea(attrs={"class": "input", "rows": 2}),
    )
    willing_to_organize = forms.BooleanField(required=False, label="I'm willing to organize or moderate this session")

    def __init__(self, *args, events=(), **kwargs):
        from apps.pitches.models import FormQuestion

        super().__init__(*args, **kwargs)
        self.event_questions = []  # [(event, [bound fields])]
        for event in events:
            names = []
            for q in FormQuestion.objects.filter(event=event, form=FormQuestion.Form.PITCH).order_by("sort_order"):
                name = f"e{event.pk}_{q.slug}"
                self.fields[name] = question_field(q)
                names.append((name, q.slug))
            self.event_questions.append((event, names))

    def answers_by_event(self) -> dict:
        return {
            event.pk: {slug: self.cleaned_data.get(name) for name, slug in names}
            for event, names in self.event_questions
        }

    def question_groups(self):
        return [(event, [self[name] for name, _ in names]) for event, names in self.event_questions]


class CoSpeakerForm(forms.Form):
    first_name = forms.CharField(
        max_length=255, required=False, widget=forms.TextInput(attrs={"class": "input", "placeholder": "First name"})
    )
    last_name = forms.CharField(
        max_length=255, required=False, widget=forms.TextInput(attrs={"class": "input", "placeholder": "Last name"})
    )
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={"class": "input", "placeholder": "Email"}))

    def clean(self):
        data = super().clean()
        filled = [data.get(f) for f in ("first_name", "last_name", "email")]
        if any(filled) and not (data.get("first_name") and data.get("email")):
            raise forms.ValidationError("Give at least a first name and an email for each co-speaker.")
        return data


CoSpeakerFormSet = forms.formset_factory(CoSpeakerForm, extra=3, max_num=8)


class CompanyForm(forms.ModelForm):
    class Meta:
        from apps.commerce.models import Company

        model = Company
        fields = ["name", "website", "description", "logo"]
        labels = {"description": "Public description"}
        help_texts = {
            "description": "Shown on published sponsor and exhibitor lists for every event you take part in.",
            "logo": "PNG or JPG; a PNG with a transparent background works best. (SVG isn't supported here.)",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != "logo":
                field.widget.attrs.setdefault("class", "input")

    def clean_logo(self):
        image = self.cleaned_data.get("logo")
        if image and hasattr(image, "size") and image.size > MAX_HEADSHOT_MB * 1024 * 1024:
            raise forms.ValidationError(f"Please upload an image under {MAX_HEADSHOT_MB} MB.")
        return image


class ContactForm(forms.ModelForm):
    class Meta:
        from apps.commerce.models import CompanyContact

        model = CompanyContact
        fields = ["name", "email", "title", "phone", "can_manage"]
        labels = {"can_manage": "Can manage the company (edit profile, add colleagues)"}

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        self.fields["email"].required = True
        self.fields["can_manage"].initial = False
        for name, field in self.fields.items():
            if name != "can_manage":
                field.widget.attrs.setdefault("class", "input")

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if self.company and self.company.contacts.filter(email__iexact=email).exists():
            raise forms.ValidationError("That person is already a contact.")
        return email


DELIVERABLE_EXTENSIONS = [
    "pdf", "png", "jpg", "jpeg", "gif", "webp", "svg", "eps", "ai", "psd",
    "doc", "docx", "txt", "rtf", "md", "ppt", "pptx", "xls", "xlsx", "csv", "zip",
]  # fmt: skip
MAX_DELIVERABLE_MB = 20


class DeliverableUploadForm(forms.Form):
    """Sponsor materials: documents, images (incl. vector logos) and zips; no
    HTML or executables, which would be served back from our storage."""

    file = forms.FileField(validators=[FileExtensionValidator(DELIVERABLE_EXTENSIONS)])

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > MAX_DELIVERABLE_MB * 1024 * 1024:
            raise forms.ValidationError(f"Please upload files under {MAX_DELIVERABLE_MB} MB.")
        return f


class ExhibitorForm(EventInfoForm):
    """The event's exhibitor questions (FormQuestion form=exhibitor); no recording-consent field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields["recording_consent"]


class EventProfileForm(forms.Form):
    """What this event's schedule shows about the speaker (their EventSpeaker copy)."""

    affiliation = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={"class": "input"}))
    job_title = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={"class": "input"}))
    bio = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "input", "rows": 6}))


class ListingForm(forms.ModelForm):
    """How one event lists the company (CompanyListing fields on a Sponsorship/ExhibitorBooking)."""

    class Meta:
        from apps.commerce.models import Sponsorship

        # Both Sponsorship and ExhibitorBooking get these fields from the abstract
        # CompanyListing, so this form works for either instance.
        model = Sponsorship
        fields = ["display_name", "description", "website", "logo"]
        labels = {"display_name": "Name", "description": "Description"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != "logo":
                field.widget.attrs.setdefault("class", "input")
